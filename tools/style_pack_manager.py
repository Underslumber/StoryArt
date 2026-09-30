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
import stat as stat_module
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
        assert_active_request_path as guard_assert_active_request_path,
        checkpoint as execution_checkpoint,
        load_guard as load_execution_guard,
        require_active_guard,
        require_execution_started,
    )
except ModuleNotFoundError:  # Direct execution as python tools\style_pack_manager.py
    from task_execution_guard import (
        GuardError as ExecutionGuardError,
        assert_active_request_path as guard_assert_active_request_path,
        checkpoint as execution_checkpoint,
        load_guard as load_execution_guard,
        require_active_guard,
        require_execution_started,
    )

try:
    from tools.reference_compatibility import ANATOMY_ROLES, load_character_identity, validate_reference_compatibility
except ModuleNotFoundError:  # Direct execution as python tools\\style_pack_manager.py
    from reference_compatibility import ANATOMY_ROLES, load_character_identity, validate_reference_compatibility

try:
    from tools.character_profile_state import STRUCTURAL as PROFILE_STRUCTURAL, ProfileStateError, confirm as confirm_profile_state, require_first_publication, load_effective, text_facts, facts_block, digest as profile_digest, verify_plan_binding
except ModuleNotFoundError:
    from character_profile_state import STRUCTURAL as PROFILE_STRUCTURAL, ProfileStateError, confirm as confirm_profile_state, require_first_publication, load_effective, text_facts, facts_block, digest as profile_digest, verify_plan_binding

try:
    from tools.generation_risk_assessor import DEFAULT_LEXICON, RiskAssessmentError, validate_assessment
    from tools.generation_call_contract import GenerationCallContractError, resolve_stage_slots
except ModuleNotFoundError:  # Direct execution as python tools\\style_pack_manager.py
    from generation_risk_assessor import DEFAULT_LEXICON, RiskAssessmentError, validate_assessment
    from generation_call_contract import GenerationCallContractError, resolve_stage_slots


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
    "ANATOMY_REVIEW", "VISIBLE_DEFECTS", "PROMPT_ADHERENCE",
    "ATTACHMENTS", "CANVAS", "STAGE_LAYER", "FACE_GEOMETRY", "BODY_SILHOUETTE",
    "BODY_PROPORTIONS", "LIMB_PROPORTIONS", "STYLE", "BODY_RENDERING_STYLE",
    "EXPRESSION", "NEUTRAL_BACKDROP", "FRONT_VIEW", "SIDE_VIEW", "BACK_VIEW",
    "SAFE_COVERAGE", "CLOTHING_TOPOLOGY", "MULTIVIEW_CONSISTENCY", "CLOTHING",
    "POSE_CONTACTS", "CAMERA", "LIGHTING", "BACKGROUND", "COMPOSITION",
    "SUBJECT_ACCURACY", "OBJECTS_AND_ACTION", "NO_UNREQUESTED_CHARACTERS", "FOCAL_HIERARCHY",
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
    "anatomy_compatibility",
    "anatomy_evidence_source",
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


def assert_active_request_path(path: str | Path) -> None:
    """Authorize request storage before reading a saved plan or output."""
    try:
        guard_assert_active_request_path(path)
    except ExecutionGuardError as error:
        raise StylePackError(str(error)) from error


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


def discover_style_packs(workspace: Path, known_pack: Path | None = None) -> list[DiscoveredStyle]:
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
    if known_pack is not None:
        candidate = known_pack.absolute()
        try:
            relative = candidate.relative_to(workspace)
        except ValueError:
            return []
        if (
            candidate.is_dir()
            and candidate.name.upper().endswith("_PROJECT_PACK")
            and len(relative.parts) == 1
            and relative.parts[0].lower() not in excluded_top_levels
            and not any(part.startswith(".") or part.lower() == "__pycache__" for part in relative.parts)
        ):
            candidates.append(candidate)
    else:
        # Packs have canonical top-level locations. Never walk request history.
        for candidate in workspace.glob("*_PROJECT_PACK"):
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


def _russian_name_stem(value: str) -> str:
    """Normalize common Russian singular case endings for character lookup."""
    words = value.split()
    if not words:
        return ""
    last = words[-1]
    if not re.search(r"[а-яё]", last, flags=re.IGNORECASE):
        return value
    for ending in ("ом", "ем", "ой", "ей", "ью", "ия", "а", "я", "у", "ю", "ы", "и", "е"):
        if len(last) > len(ending) + 1 and last.endswith(ending):
            words[-1] = last[:-len(ending)]
            break
    return " ".join(words)


def command_resolve_character(args: argparse.Namespace) -> None:
    """Resolve a named character from approved registries only.

    This intentionally reads registry/profile metadata and the registered
    approved identity files only. It never scans historical generation output.
    """
    workspace = args.workspace.resolve()
    if not workspace.is_dir():
        raise StylePackError(f"Workspace does not exist: {workspace}")
    query = " ".join(unicodedata.normalize("NFKC", args.name).split()).casefold().replace("ё", "е")
    matches: list[dict[str, str]] = []
    invalid_matches: list[dict[str, object]] = []
    for generations in sorted(workspace.glob("*_GENERATIONS"), key=lambda path: path.name.casefold()):
        registry_path = generations / "CHARACTER_REGISTRY.csv"
        if not registry_path.is_file():
            continue
        folder_slug = generations.name[: -len("_GENERATIONS")]
        pack = workspace / f"{folder_slug}_PROJECT_PACK"
        metadata_path = pack / ".style-pack.json"
        style_name = folder_slug.replace("_", " ")
        metadata_issue = ""
        if metadata_path.is_file():
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                style_name = str(metadata.get("style_name") or style_name)
            except (OSError, json.JSONDecodeError) as error:
                metadata_issue = f"Cannot read style-pack metadata: {error}"
        paths = make_paths(workspace, style_name)
        for row in read_csv(registry_path):
            if row.get("status", "").strip().upper() != "APPROVED":
                continue
            character_id = row.get("character_id", "").strip().upper()
            registered_name = " ".join(unicodedata.normalize("NFKC", row.get("name", "")).split())
            name_key = registered_name.casefold().replace("ё", "е")
            query_stem = _russian_name_stem(query)
            name_stem = _russian_name_stem(name_key)
            if query not in {name_key, character_id.casefold()} and query_stem != name_key and query != name_stem:
                continue
            if metadata_issue:
                invalid_matches.append({
                    "style_name": style_name,
                    "character_id": character_id,
                    "name": registered_name,
                    "status": "INVALID_STYLE_METADATA",
                    "metadata_path": str(metadata_path.resolve()),
                    "issue": metadata_issue,
                })
                continue
            profile = Path(row.get("profile_path", ""))
            if not profile.is_absolute():
                profile = generations / profile
            try:
                identity = _approved_profile_identity(paths, profile, character_id)
            except (OSError, StylePackError, ValueError) as error:
                invalid_matches.append({
                    "style_name": style_name,
                    "character_id": character_id,
                    "name": registered_name,
                    "status": "INVALID_APPROVED_PROFILE",
                    "profile_path": str(profile.resolve()),
                    "issue": str(error),
                })
                continue
            base = Path(row.get("approved_base", ""))
            if not base.is_absolute():
                base = generations / base
            registered_assets = {"CHARACTER_ASSEMBLY": base}
            face_assets = _character_registry_paths(paths, row.get("face_references", ""))
            body_assets = _character_registry_paths(paths, row.get("body_references", ""))
            profile_face_assets = _character_profile_paths(profile, "character_face_references")
            profile_body_assets = _character_profile_paths(profile, "character_body_references")
            if len(face_assets) != 1 or len(body_assets) < 3:
                invalid_matches.append({
                    "style_name": style_name,
                    "character_id": character_id,
                    "name": registered_name,
                    "status": "INVALID_APPROVED_ASSET_SET",
                    "profile_path": str(profile.resolve()),
                    "issue": f"Expected exactly one registered FACE and at least three BODY assets; found {len(face_assets)} FACE and {len(body_assets)} BODY.",
                    "registered_roles": {
                        "FACE": [str(path.resolve()) for path in sorted(face_assets)],
                        "BODY": [str(path.resolve()) for path in sorted(body_assets)],
                    },
                })
                continue
            if not face_assets.issubset(profile_face_assets) or not body_assets.issubset(profile_body_assets):
                invalid_matches.append({
                    "style_name": style_name,
                    "character_id": character_id,
                    "name": registered_name,
                    "status": "INVALID_PROFILE_ASSET_BINDING",
                    "profile_path": str(profile.resolve()),
                    "issue": "Registry FACE/BODY asset paths do not match the profile's approved identity references.",
                    "registered_roles": {
                        "FACE": [str(path.resolve()) for path in sorted(face_assets)],
                        "BODY": [str(path.resolve()) for path in sorted(body_assets)],
                    },
                })
                continue
            base_record = registered_approved_character_asset(paths, base) or {}
            if base_record.get("asset_role") != "BASE":
                invalid_matches.append({
                    "style_name": style_name,
                    "character_id": character_id,
                    "name": registered_name,
                    "status": "INVALID_APPROVED_ASSET_ROLE",
                    "profile_path": str(profile.resolve()),
                    "issue": f"CHARACTER_ASSEMBLY asset role must be BASE; found {base_record.get('asset_role') or 'UNREGISTERED'}.",
                    "path": str(base.resolve()),
                })
                continue
            bad_face_assets = [asset for asset in face_assets if (registered_approved_character_asset(paths, asset) or {}).get("asset_role") != "FACE"]
            if bad_face_assets:
                invalid_matches.append({
                    "style_name": style_name,
                    "character_id": character_id,
                    "name": registered_name,
                    "status": "INVALID_APPROVED_ASSET_ROLE",
                    "profile_path": str(profile.resolve()),
                    "issue": "Every registered FACE identity path must have approved asset_role FACE.",
                    "role": "FACE",
                    "paths": [str(asset.resolve()) for asset in bad_face_assets],
                })
                continue
            bad_body_assets = [asset for asset in body_assets if (registered_approved_character_asset(paths, asset) or {}).get("asset_role") != "BODY"]
            if bad_body_assets:
                invalid_matches.append({
                    "style_name": style_name,
                    "character_id": character_id,
                    "name": registered_name,
                    "status": "INVALID_APPROVED_ASSET_ROLE",
                    "profile_path": str(profile.resolve()),
                    "issue": "Every registered BODY identity path must have approved asset_role BODY.",
                    "role": "BODY",
                    "paths": [str(asset.resolve()) for asset in bad_body_assets],
                })
                continue
            registered_assets["FACE_IDENTITY"] = next(iter(face_assets))
            registered_assets.update({
                f"BODY_IDENTITY_{index + 1}": asset
                for index, asset in enumerate(sorted(body_assets, key=lambda item: item.name.casefold()))
            })
            if not base.is_file():
                invalid_matches.append({
                    "style_name": style_name,
                    "character_id": character_id,
                    "name": registered_name,
                    "status": "MISSING_APPROVED_ASSET",
                    "profile_path": str(profile.resolve()),
                    "issue": "Approved CHARACTER_ASSEMBLY path is missing.",
                    "role": "CHARACTER_ASSEMBLY",
                    "path": str(base.resolve()),
                })
                continue
            missing_assets = [role for role, asset in registered_assets.items() if not asset.is_file()]
            if missing_assets:
                invalid_matches.append({
                    "style_name": style_name,
                    "character_id": character_id,
                    "name": registered_name,
                    "status": "MISSING_APPROVED_ASSET",
                    "profile_path": str(profile.resolve()),
                    "issue": "One or more approved identity asset paths are missing.",
                    "roles": missing_assets,
                    "paths": [str(registered_assets[role].resolve()) for role in missing_assets],
                })
                continue
            role_assets, asset_index = approved_character_role_assets(paths, character_id, profile)
            try:
                confirmed = effective_character_profile(paths, character_id)
            except (OSError, ProfileStateError, StylePackError) as error:
                invalid_matches.append({"style_name": style_name, "character_id": character_id,
                                        "name": registered_name, "status": "INVALID_CONFIRMED_PROFILE",
                                        "profile_path": str(profile.resolve()), "issue": str(error)})
                continue
            matches.append({
                "style_name": style_name,
                "style_slug": folder_slug,
                "character_id": character_id,
                "name": registered_name,
                "status": "APPROVED",
                "registry_path": str(registry_path.resolve()),
                "profile_path": str(profile.resolve()),
                "approved_base": str(base.resolve()),
                "identity_assets": json.dumps(
                    {role: str(asset.resolve()) for role, asset in registered_assets.items()},
                    ensure_ascii=False,
                ),
                "role_assets": json.dumps(role_assets, ensure_ascii=False),
                "useful_sketches": json.dumps([asset for asset in role_assets if asset["role"] == "SKETCH"], ensure_ascii=False),
                "asset_index": json.dumps(asset_index, ensure_ascii=False),
                "profile_schema": str(identity.get("schema_version", "")),
                "confirmed_profile": json.dumps(confirmed, ensure_ascii=False),
            })
    if args.json:
        status = "FOUND" if matches else "FOUND_BUT_INVALID" if invalid_matches else "NOT_FOUND"
        print(json.dumps({"status": status, "query": args.name, "matches": matches, "invalid_matches": invalid_matches}, ensure_ascii=False, indent=2))
    elif matches:
        for match in matches:
            for key, value in match.items():
                print(f"{key.upper()}={value}")
            print("STATUS=APPROVED_CHARACTER_RESOLVED")
    else:
        print(f"QUERY={args.name}")
        if invalid_matches:
            print("STATUS=APPROVED_CHARACTER_MATCH_INVALID")
            for item in invalid_matches:
                print(f"INVALID_MATCH={json.dumps(item, ensure_ascii=False, separators=(',', ':'))}")
        else:
            print("STATUS=NO_APPROVED_CHARACTER_MATCH")


def command_startup_menu_template(args: argparse.Namespace) -> None:
    """Emit the canonical chooser labels and exact parameter mappings."""
    style_name = " ".join(unicodedata.normalize("NFKC", args.style_name).split())
    character_id = args.character_id.strip().upper()
    character_name = " ".join(unicodedata.normalize("NFKC", args.character_name).split())
    if not style_name:
        raise StylePackError("A resolved profile-bound style name is required.")
    if not re.fullmatch(r"CHAR_\d+", character_id):
        raise StylePackError("The standard profile-bound chooser requires an approved CHAR_NNN character id.")
    if not character_name:
        raise StylePackError("The resolved approved character name is required.")

    question = (
        f"Профиль персонажа: {character_name} ({character_id}); закреплённый стиль: {style_name}. "
        f"Процент означает точность следования стилю. Утверждённые референсы личности "
        f"{character_name} подключаются при любом варианте. BODY_REFERENCE_LIBRARY — "
        f"дополнительная библиотека тела."
    )
    if args.generation_purpose == "CHARACTER_BASE":
        question += (
            " Она может использоваться только на последующих этапах фигуры; "
            "выбор не запускает калибровку или сбор поз."
        )

    presets = (
        ("OPTION_1", f"90% стиля {style_name} + использовать BODY_REFERENCE_LIBRARY (рекомендуемый профиль StoryArt)", 90, "SELECTED", "BODY_LIBRARY_ONLY"),
        ("OPTION_2", f"90% стиля {style_name}, без BODY_REFERENCE_LIBRARY", 90, "DECLINED", "APPROVED_CHARACTER_REFERENCES"),
        ("OPTION_3", f"70% стиля {style_name}, без BODY_REFERENCE_LIBRARY — более свободная интерпретация", 70, "DECLINED", "APPROVED_CHARACTER_REFERENCES"),
    )
    options = []
    for option_id, label, fidelity, body_decision, reference_policy in presets:
        description = (
            f"{label}; style=PROJECT_STYLE:{style_name}; "
            f"reference_policy={reference_policy}; character={character_id}"
        )
        options.append({
            "id": option_id,
            "label": label,
            "description": description,
            "fidelity": fidelity,
            "aux_body_decision": body_decision,
            "resolved_user_selections": {
                "style": f"PROJECT_STYLE:{style_name}",
                "reference_policy": reference_policy,
                "character": character_id,
            },
        })
    print(json.dumps({"title": "Стиль и референсы", "question": question, "options": options}, ensure_ascii=False, indent=2))


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
        if is_image and include_files:
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


def _minimal_preparation_style_context(paths: StylePaths) -> dict[str, object]:
    """Return only pack metadata needed to prepare a request.

    Full asset inventory and collection counts belong to the explicit
    ``style-context`` command. Preparation validates chosen references and
    approved registry entries directly, so scanning every asset here adds no
    authority to those selected-source checks.
    """
    discovered = matching_discovered_style(paths)
    if discovered is None:
        raise StylePackError(f"No discovered style pack matches {paths.style_name}.")
    anchors = manifest_role_files(Path(discovered.pack_path), "ANCHOR_STYLE")
    return {
        "style_name": discovered.style_name,
        "pack_path": discovered.pack_path,
        "local_readiness": discovered.local_readiness,
        "explicit_anchor_files": anchors,
        "active_style_calibration": active_style_calibration_summary(Path(discovered.pack_path)),
    }


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


def prompt_target_anatomy(prompt: str, target_name: str = "") -> tuple[str, str]:
    """Infer anatomy only from explicit target wording; ambiguous text stays UNKNOWN."""
    text = " ".join(prompt.split())
    if not text:
        return "UNKNOWN", "UNKNOWN"
    signals: list[tuple[str, str]] = []
    for anatomy, pattern in (
        ("MALE_ANATOMY", r"\b(?:male|man|boy|мужчина|парень|мужской)\b"),
        ("FEMALE_ANATOMY", r"\b(?:female|woman|girl|женщина|девушка|женский)\b"),
    ):
        for match in re.finditer(pattern, text, re.IGNORECASE):
            if not target_name or re.search(rf"\b{re.escape(target_name)}\b.{{0,32}}\b{match.group(0)}\b|\b{match.group(0)}\b.{{0,32}}\b{re.escape(target_name)}\b", text, re.IGNORECASE):
                signals.append((anatomy, match.group(0)))
    if target_name:
        name = re.escape(target_name)
        pronouns = (("MALE_ANATOMY", r"он|его|ему|he|him|his"), ("FEMALE_ANATOMY", r"она|ее|её|ей|she|her"))
        for anatomy, pronoun in pronouns:
            match = re.search(rf"\b{name}(?:а|у|ом|е|ы|и)?\b\s*[,—:-]?\s*\b(?:{pronoun})\b", text, re.IGNORECASE)
            if match:
                signals.append((anatomy, match.group(0)))
    kinds = {item[0] for item in signals}
    if len(kinds) != 1:
        return "UNKNOWN", "UNKNOWN"
    anatomy, marker = signals[0]
    return anatomy, f"CURRENT_PROMPT_EXPLICIT:{marker}"


def _approved_profile_identity(paths: StylePaths, profile: Path, expected_id: str = "") -> dict[str, str]:
    profile = profile.resolve()
    registry = read_csv(paths.character_registry) if paths.character_registry.is_file() else []
    row = None
    for item in registry:
        registered_profile = Path(item.get("profile_path", ""))
        if not registered_profile.is_absolute():
            registered_profile = paths.generations / registered_profile
        if item.get("status", "").upper() == "APPROVED" and item.get("profile_path") and registered_profile.resolve() == profile:
            row = item
            break
    if row is None or (expected_id and row.get("character_id", "").upper() != expected_id.upper()):
        raise StylePackError("Target profile is not the matching APPROVED character-registry profile.")
    base = Path(row.get("approved_base", ""))
    if not base.is_absolute():
        base = paths.generations / base
    if not base.is_file():
        raise StylePackError("Approved character profile has no registered approved base image.")
    identity = load_character_identity(profile)
    if identity.get("character_id", "").upper() != row.get("character_id", "").upper() or identity.get("profile_status", "").upper() != "APPROVED":
        raise StylePackError("Approved profile status or character identity does not match its registry record.")
    if identity.get("target_anatomy", "UNKNOWN") not in {"MALE_ANATOMY", "FEMALE_ANATOMY", "ANDROGYNOUS_ANATOMY"}:
        identity["target_anatomy"] = "UNKNOWN"
        identity["anatomy_evidence_source"] = "UNKNOWN"
    identity["evidence_status"] = "APPROVED_REVIEWED_PROFILE"
    return identity


def compatibility_target(
    paths: StylePaths | None,
    args: argparse.Namespace,
    character_id: str | None = None,
) -> dict[str, str]:
    """Resolve target anatomy only from an approved profile or explicit prompt wording."""
    target_id = str(character_id if character_id is not None else getattr(args, "character_id", "NONE")).upper()
    profile_value = str(getattr(args, "character_profile", "") or "").strip()
    profile_identity: dict[str, str] | None = None
    if target_id.startswith("CHAR_"):
        if paths is None:
            raise StylePackError("Existing characters require their style's approved registry profile.")
        expected = character_folder(paths, target_id) / "CHARACTER_PROFILE.yaml"
        if profile_value and Path(profile_value).resolve() != expected.resolve():
            raise StylePackError("An existing character's compatibility profile must be its approved registered profile.")
        profile_identity = _approved_profile_identity(paths, expected, target_id)
    elif profile_value:
        if paths is None:
            raise StylePackError("A supplied profile requires --style-name so its approved registry record can be verified.")
        profile_identity = _approved_profile_identity(paths, Path(profile_value))

    target_name = str(getattr(args, "target_name", "") or getattr(args, "character_name", "") or (profile_identity or {}).get("character_name", "") or target_id.removeprefix("CHAR_").replace("_", " ")).strip()
    prompt_anatomy, prompt_evidence = prompt_target_anatomy(str(getattr(args, "target_prompt", "") or getattr(args, "prompt_text", "")), target_name)
    profile_anatomy = (profile_identity or {}).get("target_anatomy", "UNKNOWN")
    if profile_anatomy != "UNKNOWN" and prompt_anatomy != "UNKNOWN" and profile_anatomy != prompt_anatomy:
        raise StylePackError("Current prompt conflicts with the approved profile anatomy; clarify the target before selecting references.")
    if profile_anatomy != "UNKNOWN":
        identity = dict(profile_identity or {})
    elif prompt_anatomy != "UNKNOWN":
        identity = {
            "character_id": target_id if target_id not in {"NONE", "NEW"} else "UNKNOWN",
            "character_name": target_name or "UNKNOWN",
            "gender_identity": "UNKNOWN",
            "visible_presentation": "UNKNOWN",
            "target_anatomy": prompt_anatomy,
            "anatomy_evidence_source": prompt_evidence,
            "evidence_status": "CURRENT_PROMPT_EXPLICIT",
            "status": "PROMPT_EVIDENCE",
        }
    else:
        identity = {"character_id": target_id if target_id not in {"NONE", "NEW"} else "UNKNOWN", "target_anatomy": "UNKNOWN", "anatomy_evidence_source": "UNKNOWN", "evidence_status": "UNKNOWN"}
    manual_anatomy = str(getattr(args, "target_anatomy", "UNKNOWN") or "UNKNOWN").upper()
    if manual_anatomy != "UNKNOWN" and manual_anatomy != identity.get("target_anatomy"):
        raise StylePackError("--target-anatomy is not evidence; it must agree with an approved profile or explicit current prompt wording.")
    manual_evidence = str(getattr(args, "target_anatomy_evidence", "UNKNOWN") or "UNKNOWN").strip()
    if manual_evidence != "UNKNOWN" and manual_evidence != identity.get("anatomy_evidence_source"):
        raise StylePackError("--target-anatomy-evidence is not accepted as provenance; use an approved profile or exact prompt wording.")
    identity["target_name"] = target_name or "UNKNOWN"
    return identity


def canonical_source_character_id(paths: StylePaths, file: Path) -> str:
    """Resolve identity only from an approved profile and its registered canonical base."""
    asset = registered_approved_character_asset(paths, file)
    return str(asset.get("character_id", "UNKNOWN")) if asset else "UNKNOWN"


def enforce_reference_compatibility(
    target: dict[str, str],
    reference: dict[str, object],
    roles: Sequence[str],
) -> dict[str, object]:
    metadata = {
        "source_character_id": str(reference.get("source_character_id", "UNKNOWN")),
        "anatomy_compatibility": reference.get("anatomy_compatibility", "UNKNOWN"),
        "anatomy_evidence_source": reference.get("anatomy_evidence_source", "UNKNOWN"),
    }
    for key in ("subject_anatomy_compatibility", "subject_anatomy_evidence_source", "framing", "coverage", "source_coverage", "shot_type"):
        if key in reference:
            metadata[key] = reference[key]
    result = validate_reference_compatibility(target, metadata, roles)
    if not result["compatible"]:
        raise StylePackError(
            f"Reference compatibility {result['status']} for {reference.get('path', reference.get('ref_id', 'asset'))} "
            f"as {','.join(roles)}: {result['reason']}"
        )
    return result


def command_body_ref_context(args: argparse.Namespace) -> None:
    library, rows = load_body_reference_manifest(args.workspace)
    requested_roles = {value.upper() for value in args.allowed_role}
    context_style = str(getattr(args, "style_name", ""))
    compatibility_paths = make_paths(args.workspace, context_style) if context_style else None
    target = compatibility_target(compatibility_paths, args)
    if args.include_files and (
        target is None
        or str(target.get("target_anatomy", "UNKNOWN")).upper() == "UNKNOWN"
        or str(target.get("anatomy_evidence_source", "UNKNOWN")).strip().upper() == "UNKNOWN"
    ):
        raise StylePackError(
            "body-ref-context requires concrete target anatomy and its review-backed evidence source before returning files; "
            "supply the exact --target-prompt (and --target-name when needed) or a reviewed --character-profile with anatomy evidence."
        )
    if target is not None and not requested_roles and args.include_files:
        raise StylePackError("Specify --allowed-role with target compatibility metadata before returning reference files.")
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

    selected = []
    compatibility_filtered = 0
    compatibility_decisions: dict[str, dict[str, object]] = {}
    for row in rows:
        if not matches(row):
            continue
        if target is not None and requested_roles:
            roles = ["BODY_BUILD" if role == "AUX_BODY_BUILD" else role for role in requested_roles]
            decision = validate_reference_compatibility(target, row, roles)
            compatibility_decisions[row.get("ref_id", "")] = decision
            if not decision["compatible"]:
                compatibility_filtered += 1
                continue
        selected.append(row)
    safe_count = sum(row.get("generator_safe", "").upper() == "YES" for row in rows)
    result: dict[str, object] = {
        "library_path": str(library),
        "manifest": str(library / "BODY_REFERENCE_MANIFEST.csv"),
        "total_references": len(rows),
        "generator_safe": safe_count,
        "mask_required": sum(row.get("safety_status", "").upper() == "MASK_REQUIRED" for row in rows),
        "style_influence": "FORBIDDEN",
        "matching_references": len(selected),
        "compatibility_filtered_references": compatibility_filtered,
        "filters": {key: value for key, value in filters.items() if value},
        "allowed_roles": sorted(requested_roles),
        "compatibility_filter": "ACTIVE" if target is not None and requested_roles else "NOT_REQUESTED",
        "compatibility_decisions": compatibility_decisions,
        "status": "NO_COMPATIBLE_REFERENCES" if not selected and compatibility_filtered else "BODY_REFERENCE_CONTEXT_COMPLETE",
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
    print("STATUS=" + ("NO_COMPATIBLE_REFERENCES" if not selected and compatibility_filtered else "BODY_REFERENCE_CONTEXT_COMPLETE"))


PLAN_CATEGORIES = ("FACE", "BODY", "POSE", "CLOTHES", "LIGHTING", "BACKGROUND", "COMPOSITION")
REVIEW_CATEGORIES = ("STYLE", "SUBJECT", *PLAN_CATEGORIES)
STARTUP_CHOICES = ("OPTION_1", "OPTION_2", "OPTION_3", "CUSTOM")


def body_library_decision_supported(quote: str, decision: str) -> bool:
    """Require direct wording that supports the explicit library decision."""
    if not quote.strip() or not re.search(r"BODY_REFERENCE_LIBRARY|body\s+(?:reference\s+)?library|библиотек", quote, re.IGNORECASE):
        return False
    selected = decision.upper() == "SELECTED"
    if selected:
        return bool(re.search(r"\b(?:select(?:ed)?|use|using|enable|connect|include|with)\b|подключ|включ|использ|да", quote, re.IGNORECASE)) and not bool(
            re.search(r"\b(?:do\s+not|don't|not|without|decline(?:d)?)\b|не\s+(?:подключ|включ|использ)|без", quote, re.IGNORECASE)
        )
    if decision.upper() == "DECLINED":
        return bool(re.search(r"\b(?:do\s+not|don't|not|without|decline(?:d)?)\b|не\s+(?:подключ|включ|использ)|без", quote, re.IGNORECASE))
    return False


def menu_selection_fields(description: str) -> dict[str, str]:
    """Read exact choices shown in a complete profile, without inferring omissions."""
    fields: dict[str, str] = {}
    for part in description.split(";"):
        key, separator, value = part.strip().partition("=")
        if key not in {"style", "reference_policy", "character"}:
            continue
        if not separator or not value.strip() or key in fields:
            raise StylePackError("Menu selection fields must be unique and non-empty.")
        fields[key] = value.strip()
    if fields and set(fields) != {"style", "reference_policy", "character"}:
        raise StylePackError("A complete menu profile must record style, reference_policy, and character together.")
    return fields


def validate_menu_user_selections(
    startup: dict[str, object], selections: dict[str, object], *, allow_legacy_explicit: bool = False
) -> None:
    """Bind executable choices to the exact complete option displayed to the user."""
    if startup.get("selection_source_menu"):
        validate_menu_user_selections(
            startup["selection_source_menu"], selections, allow_legacy_explicit=allow_legacy_explicit
        )
        if startup.get("resolved_user_selections") != startup["selection_source_menu"].get("resolved_user_selections"):
            raise StylePackError("Reused choices differ from their original menu evidence.")
        return
    selected = startup.get("selected")
    option = next((row for row in startup.get("options", []) if row.get("id") == selected), {})
    fields = menu_selection_fields(str(option.get("description", "")))
    if selected in STARTUP_CHOICES[:3] and not fields:
        if not allow_legacy_explicit:
            raise StylePackError("Incomplete legacy menu: show the structured chooser for missing style/reference/character choices.")
        # Older plans only recorded the fidelity/library menu. Explicit current-chat
        # selections may fill the fields that menu never displayed.
        if any(
            not isinstance(selections.get(key), dict)
            or not str(selections[key].get("choice", "")).strip()
            or not str(selections[key].get("user_quote", "")).strip()
            for key in ("style", "reference_policy", "character")
        ):
            raise StylePackError("Legacy menu migration requires explicit style, reference_policy, and character choices with exact user quotes.")
        return
    resolved_fields = startup.get("resolved_user_selections", {})
    if fields != resolved_fields:
        raise StylePackError("Saved menu selections differ from the displayed option.")
    expected_quote = str(
        startup.get("resolved_selection_quote", startup.get("user_choice_quote", ""))
    ).strip()
    for key, value in fields.items():
        supplied = selections.get(key)
        if not isinstance(supplied, dict) or supplied.get("choice") != value:
            raise StylePackError(f"Executable {key} selection differs from the selected menu option.")
        if expected_quote and supplied.get("user_quote") != expected_quote:
            raise StylePackError(f"Executable {key} quote differs from the exact selected menu reply.")


def preset_profile_from_description(option_id: str, description: str) -> tuple[int, str]:
    menu_selection_fields(description)
    description = ";".join(part for part in description.split(";") if part.strip().partition("=")[0] not in {"style", "reference_policy", "character"})
    fidelity_matches = re.findall(r"(?<!\d)(30|50|70|90|100)\s*%?", description)
    if len(fidelity_matches) != 1:
        raise StylePackError(f"{option_id} must state exactly one fidelity percentage and BODY_REFERENCE_LIBRARY decision.")
    decision = "SELECTED" if body_library_decision_supported(description, "SELECTED") else "DECLINED" if body_library_decision_supported(description, "DECLINED") else ""
    if not decision:
        raise StylePackError(f"{option_id} must explicitly say whether BODY_REFERENCE_LIBRARY is selected or declined.")
    return int(fidelity_matches[0]), decision


def explicit_custom_profile(quote: str) -> tuple[int, str] | None:
    """Return a complete fidelity/library profile explicitly stated in free text."""
    fidelity_matches = re.findall(r"(?<!\d)(30|50|70|90|100)\s*%?(?!\d)", quote)
    if len(fidelity_matches) != 1:
        return None
    label = r"(?:BODY_REFERENCE_LIBRARY|body\s+(?:reference\s+)?library|библиотек\w*)"
    negative_before = rf"\b(?:do\s+not|don't|dont|without|decline(?:d)?|exclude)\b\s+(?:(?:to\s+)?(?:use|select|enable|connect|include)\s+)?(?:the\s+)?{label}|\bне\s+(?:использовать|используй|подключать|подключить|включать|включить|выбирать|выбрать)\s+(?:эту\s+)?{label}|\bбез\s+(?:этой\s+)?{label}|{label}\s+(?:(?:is|should\s+be|must\s+be)\s+)?(?:disabled|off|not\s+used|excluded|не\s+используется|отключена|отключен|отключить)"
    positive_before = rf"\b(?:select(?:ed)?|use|using|enable|enabled|connect|include|choose|activate|with)\b\s+(?:the\s+)?{label}|\b(?:подключить|подключи|подключение|включить|включи|использовать|используй|выбрать|выбираю)\s+(?:эту\s+)?{label}|\bс\s+(?:этой\s+)?{label}|{label}\s+(?:(?:is|should\s+be|must\s+be)\s+)?(?:enabled|on|selected|included|used|подключена|подключен|включена|включен|используется|подключить|использовать)"
    declined = bool(re.search(negative_before, quote, re.IGNORECASE))
    without_negative_library_spans = re.sub(negative_before, " ", quote, flags=re.IGNORECASE)
    selected = bool(re.search(positive_before, without_negative_library_spans, re.IGNORECASE))
    if selected == declined:
        return None
    return int(fidelity_matches[0]), "SELECTED" if selected else "DECLINED"


def parse_startup_option_descriptions(values: Sequence[str]) -> dict[str, str]:
    options: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise StylePackError("--startup-option must use OPTION_N=description.")
        option_id, description = value.split("=", 1)
        option_id = option_id.strip().upper()
        description = description.strip()
        if option_id not in STARTUP_CHOICES[:3] or not description:
            raise StylePackError("Startup menu requires non-empty OPTION_1, OPTION_2, and OPTION_3 entries.")
        if option_id in options:
            raise StylePackError(f"Duplicate startup option: {option_id}")
        options[option_id] = description
    if set(options) != set(STARTUP_CHOICES[:3]):
        raise StylePackError("Show and record exactly three complete startup profiles before CUSTOM.")
    profiles = {key: preset_profile_from_description(key, value) for key, value in options.items()}
    return options


def _normalized_menu_text(value: str) -> str:
    return " ".join(value.strip().casefold().split())


def validate_menu_choice_quote(quote: str, choice: str, displayed_label: str = "") -> None:
    normalized = quote.strip().casefold()
    if normalized in {"yes", "yeah", "yep", "ok", "okay", "да", "ага", "хорошо", "подходит"}:
        raise StylePackError("A bare yes/ok does not select a profile; record the user's numbered or named menu choice.")
    if choice == "CUSTOM" and explicit_custom_profile(quote) is not None:
        return
    number = STARTUP_CHOICES.index(choice) + 1 if choice in STARTUP_CHOICES[:3] else 4
    choices = {str(number), f"option {number}", f"option_{number}", f"вариант {number}", f"вариант_{number}"}
    if choice == "CUSTOM":
        choices.update({"custom", "указать свой вариант", "свой вариант"})
    elif choice == "OPTION_1":
        choices.update({"option_1 (recommended)", "option 1 (recommended)"})
    accepted = {_normalized_menu_text(value) for value in choices}
    if displayed_label.strip():
        accepted.add(_normalized_menu_text(displayed_label))
        # The chooser's visible label is the human-facing prefix; semicolon fields
        # are machine-readable style/reference/character bindings appended to it.
        visible_label = ";".join(
            part for part in displayed_label.split(";")
            if part.strip().partition("=")[0].strip() not in {"style", "reference_policy", "character"}
        )
        accepted.add(_normalized_menu_text(visible_label))
    if _normalized_menu_text(quote) not in accepted:
        raise StylePackError("Menu selection quote must exactly identify the selected visible option, not merely approve the preceding style name.")


def validate_startup_profile_evidence(startup: dict[str, object], fidelity: int | None = None) -> tuple[int, str]:
    """Validate the saved selection against the evidence type that produced it."""
    resolved = startup.get("resolved_parameters")
    if not isinstance(resolved, dict):
        raise StylePackError("The source plan has no verifiable startup profile parameters.")
    resolved_fidelity = int(resolved.get("fidelity", -1))
    decision = str(resolved.get("aux_body_decision", "")).upper()
    if resolved_fidelity not in {30, 50, 70, 90, 100} or decision not in {"SELECTED", "DECLINED"}:
        raise StylePackError("The source plan has an unresolved legacy profile; same-chat reuse cannot fabricate the missing BODY_REFERENCE_LIBRARY decision.")
    if fidelity is not None and resolved_fidelity != fidelity:
        raise StylePackError("Saved startup profile fidelity does not match the prepared reference plan.")

    state = str(startup.get("selection_state", ""))
    origin_state = str(startup.get("selection_origin_state", state))
    provenance = startup.get("confirmed_provenance")
    library = startup.get("body_library_provenance")
    if not isinstance(provenance, dict) or not all(str(provenance.get(key, "")).strip() for key in ("chat_id", "message_id", "quote")):
        raise StylePackError("Startup profiles require same-chat message and quote provenance.")
    if not isinstance(library, dict) or (
        library.get("chat_id") != provenance.get("chat_id")
        or not str(library.get("message_id", "")).strip()
        or str(library.get("decision", "")).upper() != decision
    ):
        raise StylePackError("The source plan does not contain same-chat evidence for its BODY_REFERENCE_LIBRARY decision.")

    if origin_state in {"USER_CONFIRMED_FROM_TEXT_MENU", "NEW_SELECTION"} and startup.get("menu_contract") == "THREE_AI_PRESETS_PLUS_CUSTOM":
        options = startup.get("options")
        if not isinstance(options, list) or [str(row.get("id", "")).upper() for row in options if isinstance(row, dict)] != [*STARTUP_CHOICES]:
            raise StylePackError("The source menu profile cannot be verified for same-chat reuse.")
        option_map = {str(row.get("id", "")).upper(): str(row.get("description", "")) for row in options if isinstance(row, dict)}
        profiles = {key: preset_profile_from_description(key, option_map[key]) for key in STARTUP_CHOICES[:3]}
        choice = str(startup.get("selected", "")).upper()
        user_choice_quote = str(startup.get("user_choice_quote", "")).strip()
        selected_description = option_map.get(choice, "")
        validate_menu_choice_quote(user_choice_quote, choice, selected_description)
        if str(provenance.get("quote", "")).strip() != user_choice_quote:
            raise StylePackError("The recorded menu-choice evidence differs from the exact user reply.")
        if choice == "CUSTOM":
            custom_quote = str(startup.get("custom_parameters_user_quote", "")).strip()
            profile = (resolved_fidelity, decision)
            if not custom_quote or str(provenance.get("quote", "")).strip() != user_choice_quote:
                raise StylePackError("CUSTOM requires its exact menu choice and a complete user-supplied profile quote.")
            if explicit_custom_profile(custom_quote) != profile:
                raise StylePackError("CUSTOM profile evidence must state its fidelity and BODY_REFERENCE_LIBRARY decision.")
            choice_profile = explicit_custom_profile(user_choice_quote)
            if choice_profile is not None and choice_profile != profile:
                raise StylePackError("Natural CUSTOM choice quote does not match its separately recorded full profile quote.")
            if str(library.get("quote", "")).strip() != custom_quote:
                raise StylePackError("CUSTOM library evidence must retain the user's complete profile quote.")
        elif choice in STARTUP_CHOICES[:3]:
            profile = profiles[choice]
            if str(library.get("quote", "")).strip() != user_choice_quote:
                raise StylePackError("The menu's BODY_REFERENCE_LIBRARY evidence must retain the exact selected-option reply.")
        else:
            raise StylePackError("The saved startup choice is not one of the displayed profiles.")
        if profile != (resolved_fidelity, decision):
            raise StylePackError("The saved menu choice does not match its resolved fidelity and BODY_REFERENCE_LIBRARY profile.")
        return resolved_fidelity, decision

    quote = str(provenance.get("quote", ""))
    library_quote = str(library.get("quote", ""))
    if not re.search(rf"(?<!\d){resolved_fidelity}\s*%?(?!\d)", quote):
        raise StylePackError("Direct confirmation provenance does not support the prepared fidelity.")
    if not body_library_decision_supported(library_quote, decision):
        raise StylePackError("The source plan does not contain same-chat wording supporting its BODY_REFERENCE_LIBRARY decision.")
    return resolved_fidelity, decision


def startup_clarification_requirements(args: argparse.Namespace) -> list[str]:
    """Return only critical startup values that are genuinely unresolved."""
    missing: list[str] = []
    fidelity = getattr(args, "fidelity", None)
    if fidelity not in {30, 50, 70, 90, 100}:
        missing.append("fidelity")
    selection_mode = getattr(args, "startup_selection_mode", "").upper()
    if selection_mode in {"DIRECT_CONFIRMATION", "USER_CONFIRMATION", "NEW"}:
        for argument, label in (
            ("confirmed_chat_id", "chat_id"),
            ("confirmed_message_id", "message_id"),
        ):
            if not str(getattr(args, argument, "")).strip():
                missing.append(label)
    if selection_mode == "DIRECT_CONFIRMATION":
        decision = str(getattr(args, "aux_body_decision", "NOT_SELECTED")).upper()
        if decision not in {"SELECTED", "DECLINED"}:
            missing.append("body_library_decision")
        quote = str(getattr(args, "confirmed_parameters_user_quote", ""))
        library_quote = str(getattr(args, "confirmed_body_library_user_quote", ""))
        if decision in {"SELECTED", "DECLINED"} and not (
            body_library_decision_supported(quote, decision)
            or body_library_decision_supported(library_quote, decision)
        ):
            missing.append("body_library_confirmation")
        for argument, label in (
            ("confirmed_parameters_user_quote", "fidelity_quote"),
        ):
            if not str(getattr(args, argument, "")).strip():
                missing.append(label)
        if library_quote.strip() and not str(getattr(args, "confirmed_body_library_message_id", "")).strip():
            missing.append("body_library_message_id")
        if library_quote.strip() and not str(getattr(args, "confirmed_body_library_chat_id", "")).strip():
            missing.append("body_library_chat_id")
    elif selection_mode in {"USER_CONFIRMATION", "NEW"}:
        if getattr(args, "startup_choice", "").upper() not in STARTUP_CHOICES:
            missing.append("menu_selection")
    return missing
RISK_LABEL_RE = re.compile(r"^D(?:[1-9]|10)$", re.IGNORECASE)


def parse_startup_interaction(args: argparse.Namespace) -> dict[str, object]:
    selection_mode = args.startup_selection_mode.upper()
    if selection_mode == "DIRECT_CONFIRMATION":
        missing = startup_clarification_requirements(args)
        if missing:
            raise StylePackError("Missing required profile confirmation only: " + ", ".join(missing))
        quote = args.confirmed_parameters_user_quote.strip()
        if not re.search(rf"(?<!\d){args.fidelity}\s*%?(?!\d)", quote):
            raise StylePackError("The direct confirmation quote must support the selected fidelity value.")
        decision = args.aux_body_decision.upper()
        body_quote = args.confirmed_body_library_user_quote.strip() or quote
        if not body_library_decision_supported(body_quote, decision):
            raise StylePackError("Direct profile confirmation must explicitly select or decline BODY_REFERENCE_LIBRARY.")
        body_chat_id = str(getattr(args, "confirmed_body_library_chat_id", "")).strip() or args.confirmed_chat_id.strip()
        body_message_id = str(getattr(args, "confirmed_body_library_message_id", "")).strip() or args.confirmed_message_id.strip()
        style_confirmed = style_name_is_explicitly_mentioned(args.style_name, quote)
        if body_chat_id != args.confirmed_chat_id.strip():
            raise StylePackError("Fidelity and BODY_REFERENCE_LIBRARY confirmations must come from the same current chat.")
        if body_quote != quote and not body_message_id:
            raise StylePackError("A separate BODY_REFERENCE_LIBRARY reply requires its message id.")
        return {
            "menu_contract": "DIRECT_CONFIRMED_PARAMETERS",
            "options": [],
            "selected": "DIRECT_CONFIRMED",
            "selection_state": "DIRECT_PARAMETERS_CONFIRMED",
            "menu_presented_this_turn": False,
            "menu_surface_this_turn": "NONE_DIRECT_CURRENT_CHAT",
            "user_requested_reselection": bool(args.user_requested_reselection),
            "user_choice_quote": quote,
            "parameter_source": "DIRECT_USER_CONFIRMED_CURRENT_CHAT",
            "confirmed_provenance": {
                "chat_id": args.confirmed_chat_id.strip(),
                "message_id": args.confirmed_message_id.strip(),
                "quote": quote,
            },
            "body_library_provenance": {
                "chat_id": body_chat_id,
                "message_id": body_message_id,
                "quote": body_quote,
                "decision": decision,
            },
            "resolved_parameters": {
                "fidelity": args.fidelity,
                "aux_body_decision": decision,
            },
            "profile_confirmation_complete": True,
            "same_profile_reconfirmation_forbidden": True,
            "style_confirmation_complete": style_confirmed,
            "confirmed_style_name": args.style_name if style_confirmed else "",
            "custom_parameters_user_quote": "",
            "optional_follow_up_questions_forbidden": True,
        }

    if selection_mode == "REUSE":
        if not args.reuse_startup_from:
            raise StylePackError("REUSE requires --reuse-startup-from pointing to the previous same-chat REFERENCE_PLAN.json.")
        source_path = Path(args.reuse_startup_from).resolve()
        assert_active_request_path(source_path)
        if not source_path.is_file():
            raise StylePackError(f"Reused startup plan does not exist: {source_path}")
        try:
            source_plan = json.loads(source_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise StylePackError(f"Cannot read reused startup plan: {error}") from error
        if style_slug(str(source_plan.get("style_name", ""))) != style_slug(args.style_name):
            raise StylePackError("A same-chat startup selection may be reused only for the same selected style.")
        source_startup = source_plan.get("startup_parameter_selection")
        if not isinstance(source_startup, dict) or source_startup.get("selection_state") not in {
            "DIRECT_PARAMETERS_CONFIRMED",
            "REUSED_IN_SAME_CHAT", "REUSED_WITH_USER_RESELECTION",
            "USER_CONFIRMED_FROM_TEXT_MENU", "NEW_SELECTION",
        }:
            raise StylePackError("The source plan has no verifiable same-chat startup parameters.")
        provenance = source_startup.get("confirmed_provenance")
        if (
            not isinstance(provenance, dict)
            or not str(getattr(args, "reuse_chat_id", "")).strip()
            or not str(getattr(args, "reuse_message_id", "")).strip()
            or provenance.get("chat_id") != args.reuse_chat_id.strip()
            or provenance.get("message_id") != args.reuse_message_id.strip()
        ):
            raise StylePackError("REUSE requires matching chat and message provenance; a saved plan alone cannot prove same-chat continuity.")
        resolved_fidelity, resolved_decision = validate_startup_profile_evidence(source_startup)
        profile_changed = (
            resolved_fidelity != args.fidelity
            or resolved_decision != str(args.aux_body_decision).upper()
        )
        if profile_changed and not args.user_requested_reselection:
            raise StylePackError("REUSE must preserve the previous profile unless the user explicitly requests reselection.")
        correction_quote = str(getattr(args, "confirmed_parameters_user_quote", "")).strip()
        if profile_changed and not correction_quote:
            raise StylePackError("A reused profile change requires the user's exact correction in --confirmed-parameters-user-quote.")
        if profile_changed and (
            str(getattr(args, "confirmed_chat_id", "")).strip() != str(provenance.get("chat_id", ""))
            or not str(getattr(args, "confirmed_message_id", "")).strip()
        ):
            raise StylePackError("A same-chat reselection requires the current chat id and correcting message id.")
        if profile_changed and (
            not re.search(rf"(?<!\d){args.fidelity}\s*%?(?!\d)", correction_quote)
            or not body_library_decision_supported(
                str(getattr(args, "confirmed_body_library_user_quote", "")) or correction_quote,
                str(args.aux_body_decision),
            )
        ):
            raise StylePackError("Same-chat reselection must directly support both its new fidelity and BODY_REFERENCE_LIBRARY decision.")
        reused = json.loads(json.dumps(source_startup, ensure_ascii=False))
        if profile_changed:
            reused["resolved_parameters"] = {
                **source_startup["resolved_parameters"],
                "fidelity": args.fidelity,
                "aux_body_decision": args.aux_body_decision.upper(),
            }
        reused.update({
            "selection_state": "REUSED_WITH_USER_RESELECTION" if profile_changed else "REUSED_IN_SAME_CHAT",
            "reused_from_plan": str(source_path),
            "menu_presented_this_turn": False,
            "menu_surface_this_turn": "NOT_PRESENTED_REUSED_SELECTION",
            "user_requested_reselection": bool(profile_changed),
            "profile_confirmation_complete": True,
            "same_profile_reconfirmation_forbidden": True,
            "custom_parameters_user_quote": str(source_startup.get("custom_parameters_user_quote", "")),
        })
        if profile_changed:
            if source_startup.get("resolved_user_selections"):
                reused["selection_source_menu"] = source_startup.get("selection_source_menu", source_startup)
                reused["resolved_selection_quote"] = source_startup.get("resolved_selection_quote", source_startup["user_choice_quote"])
            reused["parameter_source"] = "DIRECT_USER_CORRECTION_IN_SAME_CHAT"
            reused["user_choice_quote"] = correction_quote
            reused["menu_contract"] = "DIRECT_CONFIRMED_PARAMETERS"
            reused["selected"] = "DIRECT_CONFIRMED"
            reused["options"] = []
            reused["selection_origin_state"] = "DIRECT_PARAMETERS_CONFIRMED"
            reused["confirmed_provenance"] = {
                "chat_id": args.confirmed_chat_id.strip(),
                "message_id": args.confirmed_message_id.strip(),
                "quote": correction_quote,
            }
            library_quote = str(getattr(args, "confirmed_body_library_user_quote", "")).strip() or correction_quote
            reused["body_library_provenance"] = {
                "chat_id": args.confirmed_chat_id.strip(),
                "message_id": str(getattr(args, "confirmed_body_library_message_id", "")).strip() or str(args.confirmed_message_id).strip(),
                "quote": library_quote,
                "decision": str(args.aux_body_decision).upper(),
            }
        return reused

    if selection_mode == "AUTO_DEFAULT":
        raise StylePackError("AUTO_DEFAULT is forbidden for new plans.")

    if selection_mode in {"USER_CONFIRMATION", "NEW"}:
        if selection_mode == "USER_CONFIRMATION" and args.startup_menu_surface != "TEXT_NUMBERED_MENU":
            raise StylePackError(
                "USER_CONFIRMATION requires a visible numbered menu recorded as TEXT_NUMBERED_MENU."
            )
        if args.startup_menu_surface not in {"TEXT_NUMBERED_MENU", "NATIVE_CONTEXT_MENU"}:
            raise StylePackError("A visible numbered or native startup menu must be recorded.")
        choice = args.startup_choice.upper()
        if choice not in STARTUP_CHOICES:
            raise StylePackError("USER_CONFIRMATION requires OPTION_1, OPTION_2, OPTION_3, or CUSTOM.")
        user_quote = args.startup_choice_user_quote.strip()
        if not user_quote:
            raise StylePackError("USER_CONFIRMATION requires the user's explicit reply in --startup-choice-user-quote.")
        options = parse_startup_option_descriptions(args.startup_option)
        validate_menu_choice_quote(user_quote, choice, options.get(choice, ""))
        if any(not menu_selection_fields(description) for description in options.values()):
            raise StylePackError("Every new preset must include style, reference_policy, and character; show the complete structured chooser.")
        custom_quote = args.custom_parameters_user_quote.strip()
        if choice == "CUSTOM":
            quoted_choice_profile = explicit_custom_profile(user_quote)
            if quoted_choice_profile is not None and quoted_choice_profile != (args.fidelity, args.aux_body_decision.upper()):
                raise StylePackError("Natural CUSTOM choice quote does not match the prepared fidelity and BODY_REFERENCE_LIBRARY decision.")
            if not custom_quote and quoted_choice_profile is not None:
                custom_quote = user_quote
            if not custom_quote or explicit_custom_profile(custom_quote) != (args.fidelity, args.aux_body_decision.upper()):
                raise StylePackError("CUSTOM requires the user's full profile quote, including the selected fidelity.")
            resolved_profile = (args.fidelity, args.aux_body_decision.upper())
        else:
            resolved_profile = preset_profile_from_description(choice, options[choice])
            if (args.fidelity, args.aux_body_decision.upper()) != resolved_profile:
                raise StylePackError(
                    f"The selected {choice} profile is {resolved_profile[0]}% with BODY_REFERENCE_LIBRARY "
                    f"{resolved_profile[1]}; --fidelity and --aux-body-decision must match it."
                )
        if choice != "CUSTOM" and custom_quote:
            raise StylePackError("--custom-parameters-user-quote is valid only when CUSTOM was selected.")
        if not args.confirmed_chat_id.strip() or not args.confirmed_message_id.strip():
            raise StylePackError("Menu selection requires the current chat id and the message id containing the user's choice.")
        quote = user_quote
        selected_fields = menu_selection_fields(options[choice]) if choice != "CUSTOM" else {}
        selected_style = selected_fields.get("style", "")
        style_matches = selected_style == f"PROJECT_STYLE:{args.style_name}" or selected_style == "GENERATOR_DEFAULT"
        if selected_fields and (
            not style_matches
            or selected_fields["character"] != str(getattr(args, "character_id", "NONE"))
            or (
                selected_style == "GENERATOR_DEFAULT"
                and not re.fullmatch(r"CHAR_\d+", str(getattr(args, "character_id", "NONE")).upper())
            )
            or (
                selected_style == "GENERATOR_DEFAULT"
                and selected_fields.get("reference_policy") not in {
                    "BODY_LIBRARY_ONLY", "APPROVED_CHARACTER_REFERENCES",
                    "APPROVED_PLUS_USER_REFERENCES", "USER_ATTACHED_REFERENCES",
                }
            )
        ):
            raise StylePackError("Selected menu style, reference policy, or character differs from the prepared request.")
        menu_state = "USER_CONFIRMED_FROM_TEXT_MENU" if args.startup_menu_surface == "TEXT_NUMBERED_MENU" else "NEW_SELECTION"
        return {
            "menu_contract": "THREE_AI_PRESETS_PLUS_CUSTOM",
            "options": [
                {"id": key, "description": options[key], "recommended": key == "OPTION_1"}
                for key in STARTUP_CHOICES[:3]
            ]
            + [{"id": "CUSTOM", "description": "Указать свой вариант"}],
            "selected": choice,
            "selection_state": menu_state,
            "selection_origin_state": menu_state,
            "menu_presented_this_turn": True,
            "menu_surface_this_turn": args.startup_menu_surface,
            "user_requested_reselection": args.user_requested_reselection,
            "user_choice_quote": user_quote,
            "parameter_source": "USER_CONFIRMATION_FROM_TEXT_MENU" if args.startup_menu_surface == "TEXT_NUMBERED_MENU" else "USER_CONFIRMED_NATIVE_MENU",
            "confirmed_provenance": {
                "chat_id": args.confirmed_chat_id.strip(),
                "message_id": args.confirmed_message_id.strip(),
                "quote": quote,
            },
            "body_library_provenance": {
                "chat_id": args.confirmed_chat_id.strip(),
                "message_id": args.confirmed_message_id.strip(),
                "quote": custom_quote if choice == "CUSTOM" else quote,
                "decision": resolved_profile[1],
            },
            "resolved_parameters": {"fidelity": resolved_profile[0], "aux_body_decision": resolved_profile[1]},
            "resolved_user_selections": selected_fields,
            "profile_confirmation_complete": True,
            "same_profile_reconfirmation_forbidden": True,
            "style_confirmation_complete": True,
            "confirmed_style_name": (
                args.style_name if selected_fields.get("style") == f"PROJECT_STYLE:{args.style_name}" else ""
            ),
            "custom_parameters_user_quote": custom_quote,
            "custom_description_treated_as_complete": choice == "CUSTOM",
            "follow_up_allowed_only_for_genuinely_missing_required_information": True,
            "optional_follow_up_questions_forbidden": choice == "CUSTOM",
            "numeric_values_were_not_requested_before_menu_choice": True,
        }

    raise StylePackError(f"Unknown startup selection mode: {selection_mode}")


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
    prompt_text: str,
    slots: Sequence[dict[str, object]],
) -> tuple[Path, dict[str, object]]:
    path = Path(value).resolve()
    if not path.is_file():
        raise StylePackError(f"Risk assessment does not exist: {path}")
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise StylePackError(f"Cannot read risk assessment: {error}") from error
    if not isinstance(report, dict) or report.get("schema_version") != 1 or not RISK_LABEL_RE.fullmatch(str(report.get("generation_risk", ""))):
        raise StylePackError("Risk assessment must use schema 1 and contain generation_risk D1-D10.")
    prompt = report.get("revised_prompt") or report.get("original_prompt")
    if not isinstance(prompt, dict) or not RISK_LABEL_RE.fullmatch(str(prompt.get("risk", ""))):
        raise StylePackError("Risk assessment has no analyzed prompt with a D1-D10 result.")
    actual_references = []
    for slot in slots:
        file = Path(str(slot.get("path", ""))).resolve()
        digest = str(slot.get("sha256", "")).lower()
        if not file.is_file() or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise StylePackError(f"Executable call reference changed or is missing: {file}")
        actual_references.append({"path": str(file), "sha256": digest, "active_roles": list(slot.get("active_roles", []))})
    try:
        validate_assessment(report, prompt_text, actual_references, DEFAULT_LEXICON)
    except RiskAssessmentError as error:
        raise StylePackError(f"Exact-call risk assessment is invalid: {error}") from error
    return path, report


def validated_stage_outputs(paths: StylePaths, plan_path: Path, plan: dict[str, object]) -> dict[str, dict[str, object]]:
    """Load latest QA-passed STAGING outputs for this exact request and plan."""
    latest_by_stage: dict[str, dict[str, str]] = {}
    if not paths.generation_manifest.is_file():
        return {}
    request_id = str(plan.get("request_id", ""))
    for row in read_csv(paths.generation_manifest):
        if row.get("request_id") != request_id:
            continue
        stage_match = re.search(r"\[STAGE_ID=([^\]]+)\]", row.get("notes", ""))
        stage_id = stage_match.group(1) if stage_match else ""
        if stage_id:
            latest_by_stage[stage_id] = row
    result: dict[str, dict[str, object]] = {}
    expected_character = str(plan.get("character_id", ""))
    for stage_id, row in latest_by_stage.items():
        if row.get("status", "").upper() != "STAGING" or row.get("reference_plan") != str(plan_path.resolve()):
            continue
        try:
            evidence = generation_qa_evidence(row)
        except (OSError, ValueError, TypeError):
            continue
        style_file = Path(row.get("style_file", ""))
        try:
            contract_path = Path(str(evidence.get("qa_contract", ""))) if isinstance(evidence, dict) else Path()
            contract = json.loads(contract_path.read_text(encoding="utf-8")) if contract_path.is_file() else {}
            snapshot_path = Path(str(contract.get("plan_snapshot", ""))) if isinstance(contract, dict) else Path()
            snapshot = json.loads(snapshot_path.read_text(encoding="utf-8")) if snapshot_path.is_file() else {}
        except (OSError, json.JSONDecodeError):
            continue
        if (
            row.get("status", "").upper() != "STAGING"
            or not generation_has_passed_qa(row)
            or not evidence
            or not style_file.is_file()
            or row.get("reference_plan") != str(plan_path.resolve())
            or str(contract.get("stage_id", "")).upper() != stage_id.upper()
            or str(snapshot.get("request_id", "")) != request_id
            or str(snapshot.get("character_id", "")) != expected_character
            or str(row.get("character_id", "")) != expected_character
        ):
            continue
        result[stage_id] = {
            "path": str(style_file.resolve()),
            "sha256": sha256(style_file),
            "request_id": request_id,
            "reference_plan": str(plan_path.resolve()),
            "stage_id": stage_id,
            "status": "STAGING",
            "qa_passed": True,
            "plan_snapshot": str(snapshot_path.resolve()),
            "plan_snapshot_sha256": sha256(snapshot_path),
        }
    return result


def resolve_execution_call(
    paths: StylePaths,
    plan_path: Path,
    plan: dict[str, object],
    stage_id: str,
    prompt_text: str,
    risk_assessment_path: str,
    *,
    resolved_slots: list[dict[str, object]] | None = None,
    resolved_stage_outputs: dict[str, dict[str, object]] | None = None,
) -> tuple[dict[str, object], Path, dict[str, object]]:
    stage_outputs = validated_stage_outputs(paths, plan_path, plan)
    if resolved_stage_outputs is not None and stage_outputs != resolved_stage_outputs:
        raise StylePackError("Validated stage outputs changed since reference resolution.")
    if resolved_slots is None:
        slots = resolve_call_slots(paths, plan_path, plan, stage_id, stage_outputs)
    else:
        assert_profile_plan_current(paths, plan)
        slots = [dict(slot) for slot in resolved_slots]
        for slot in slots:
            file = Path(str(slot.get("path", ""))).resolve()
            if not file.is_file() or sha256(file) != str(slot.get("sha256", "")).lower():
                raise StylePackError(f"Resolved call slot is missing or changed: {file}")
            validate_resolved_slot_compatibility(paths, plan, slot, file)
    workflow = plan.get("generation_workflow", {})
    planned_stage = next(
        (item for item in workflow.get("stages", []) if item.get("stage_id") == stage_id),
        {},
    ) if workflow.get("mode") == "MULTI_STAGE" else {}
    stage_output_bindings: list[dict[str, object]] = []
    targeted_pack_bindings: list[dict[str, object]] = []
    bound_stage_ids: set[str] = set()
    for planned_slot in planned_stage.get("slots", []):
        slot_path = str(planned_slot.get("path", ""))
        stage_match = re.fullmatch(r"<STAGE_OUTPUT:([^<>]+)>", slot_path)
        if stage_match:
            source_id = stage_match.group(1)
            output = stage_outputs.get(source_id)
            if output and source_id not in bound_stage_ids:
                stage_output_bindings.append(dict(output))
                bound_stage_ids.add(source_id)
        pack_match = re.fullmatch(r"<TARGETED_STAGE_PACK:([^<>]+)>", slot_path)
        if pack_match:
            source_ids = [value for value in pack_match.group(1).split("+") if value]
            for source_id in source_ids:
                output = stage_outputs.get(source_id)
                if output and source_id not in bound_stage_ids:
                    stage_output_bindings.append(dict(output))
                    bound_stage_ids.add(source_id)
            expected_sources = [stage_outputs.get(source_id, {}) for source_id in source_ids]
            pack_slot = None
            for candidate in slots:
                manifest_value = candidate.get("manifest_path")
                if not manifest_value:
                    continue
                try:
                    manifest = json.loads(Path(str(manifest_value)).read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                source_manifest = manifest.get("sources", [])
                if (
                    manifest.get("request_id") == plan.get("request_id")
                    and [str(row.get("sha256", "")).lower() for row in source_manifest]
                    == [str(row.get("sha256", "")).lower() for row in expected_sources]
                    and [str(row.get("path", "")).casefold() for row in source_manifest]
                    == [str(row.get("path", "")).casefold() for row in expected_sources]
                ):
                    pack_slot = candidate
                    break
            if pack_slot:
                targeted_pack_bindings.append({
                    "request_id": str(plan.get("request_id", "")),
                    "stage_id": stage_id,
                    "placeholder": slot_path,
                    "source_stage_ids": source_ids,
                    "path": pack_slot["path"],
                    "sha256": pack_slot["sha256"],
                    "manifest_path": pack_slot["manifest_path"],
                })
    risk_path, report = load_and_validate_risk_assessment(risk_assessment_path, prompt_text, slots)
    call = {
        "request_id": str(plan.get("request_id", "")),
        "stage_id": stage_id,
        "prompt": {"text": prompt_text, "text_sha256": hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()},
        "slots": [
            {key: slot[key] for key in ("path", "sha256", "active_roles", "slot", "physically_attach", "manifest_path") if key in slot}
            for slot in slots
        ],
        "stage_output_bindings": stage_output_bindings,
        "targeted_pack_bindings": targeted_pack_bindings,
        "risk_assessment": {"path": str(risk_path), **report},
        "confirmed_character_profile": plan.get("confirmed_character_profile"),
        "profile_default_application": plan.get("profile_default_application"),
        "profile_default_exceptions": plan.get("profile_default_exceptions"),
    }
    return call, risk_path, report


def resolve_call_slots(
    paths: StylePaths,
    plan_path: Path,
    plan: dict[str, object],
    stage_id: str,
    stage_outputs: dict[str, dict[str, object]] | None = None,
) -> list[dict[str, object]]:
    assert_profile_plan_current(paths, plan)
    request_folder = plan_path.parent
    try:
        slots = resolve_stage_slots(
            plan,
            stage_id,
            stage_outputs if stage_outputs is not None else validated_stage_outputs(paths, plan_path, plan),
            request_folder / "TECHNICAL_REFERENCES",
        )
    except GenerationCallContractError as error:
        raise StylePackError(f"Executable stage references are unresolved: {error}") from error
    for slot in slots:
        file = Path(str(slot.get("path", ""))).resolve()
        if not file.is_file() or sha256(file) != str(slot.get("sha256", "")).lower():
            raise StylePackError(f"Resolved call slot is missing or changed: {file}")
        validate_resolved_slot_compatibility(paths, plan, slot, file)
    return slots


def validate_resolved_slot_compatibility(
    paths: StylePaths,
    plan: dict[str, object],
    slot: dict[str, object],
    file: Path,
) -> None:
    """Recheck explicit source metadata at the final physical-attachment boundary."""
    target_block = plan.get("target_identity_compatibility", {})
    target = target_block.get("target", {}) if isinstance(target_block, dict) else {}
    planned_compatibility = target_block.get("references", []) if isinstance(target_block, dict) else []
    roles = [str(role).upper() for role in slot.get("active_roles", [])]
    role_aliases = {
        "PRIMARY_FACE": "FACE",
        "SUPPORTING_FACE": "STYLE",
        "EXPRESSION": "FACE",
        "COVERAGE_FRONT": "CLOTHES",
        "COVERAGE_SIDE": "CLOTHES",
        "COVERAGE_BACK": "CLOTHES",
    }
    roles = [role_aliases.get(role, role) for role in roles]
    selected = plan.get("selected_references", {})
    if is_relative_to(file, paths.pack) and "STYLE" in roles:
        candidates = selected.get("style", []) if isinstance(selected, dict) else []
        candidates = candidates if isinstance(candidates, list) else [candidates]
        request_local = next(
            (
                item for item in candidates
                if isinstance(item, dict)
                and item.get("path")
                and Path(str(item["path"])).resolve() == file
                and item.get("status") == "REQUEST_LOCAL_STYLE_CANDIDATE"
            ),
            None,
        )
        try:
            relative = file.relative_to(paths.pack)
        except ValueError:
            relative = Path()
        if request_local is not None:
            if (
                request_local.get("reference_scope") != "CURRENT_REQUEST_ONLY"
                or request_local.get("permanent_anchor") is not False
                or not has_positive_master_style_manifest_entry(paths, relative)
            ):
                raise StylePackError(
                    f"Attached STYLE reference is no longer a valid request-local candidate: {file}"
                )
        elif style_formation_status(paths) != "FORMED":
            raise StylePackError(
                f"Attached STYLE reference uses an unformed style-pack source without a current-request candidate marker: {file}"
            )
    body_library = (paths.workspace / BODY_LIBRARY_NAME).resolve()
    in_body_library = is_relative_to(file, body_library)
    body_row: dict[str, str] | None = None
    manifest_path = body_library / "BODY_REFERENCE_MANIFEST.csv"
    manifest_digest = ""
    if in_body_library:
        try:
            _, body_rows = load_body_reference_manifest(paths.workspace)
            body_row = next(
                (item for item in body_rows if item.get("generator_path") and (body_library / item["generator_path"]).resolve() == file),
                None,
            )
            manifest_digest = sha256(manifest_path)
        except (StylePackError, OSError) as error:
            raise StylePackError(f"Cannot revalidate attached body-library reference: {error}") from error
        if body_row is None:
            raise StylePackError(f"Attached body reference is no longer present in the reviewed manifest: {file}")

    pose_only = bool(roles) and set(roles).issubset({"AUX_POSE", "POSE", "POSE_SOFT"})
    framing = str((body_row or {}).get("framing", "")).upper().replace("-", "_").replace(" ", "_")
    full_body = framing in {"FULL", "FULL_BODY", "FULLBODY", "FULL_LENGTH", "FULL_LENGTH_BODY"}
    anatomy_visible = bool(set(roles) & ANATOMY_ROLES) or "AUX_OBJECT_INTERACTION" in roles or (full_body and not pose_only)
    if in_body_library:
        auxiliary = plan.get("auxiliary_body_references", [])
        matching_auxiliary = next(
            (item for item in auxiliary if isinstance(item, dict) and item.get("path") and Path(str(item["path"])).resolve() == file),
            None,
        ) if isinstance(auxiliary, list) else None
        if matching_auxiliary is None:
            raise StylePackError(f"Attached body reference has no matching planned auxiliary record: {file}")
        if str(matching_auxiliary.get("manifest_sha256", "")).lower() != manifest_digest.lower():
            raise StylePackError(f"Attached body reference was selected against a different or unrecorded manifest hash: {file}")
        if str(matching_auxiliary.get("sha256", "")).lower() != sha256(file).lower():
            raise StylePackError(f"Attached body reference hash differs from its planned auxiliary record: {file}")
        allowed_roles = {item.strip() for item in str(body_row.get("allowed_roles", "")).upper().split(";") if item.strip()}
        if body_row.get("generator_safe", "").upper() != "YES" or not set(roles).issubset(allowed_roles):
            raise StylePackError(f"Attached body reference is no longer generator-safe for its active role: {file}")
        matching_compatibility = next(
            (item for item in planned_compatibility if isinstance(item, dict) and item.get("path") and Path(str(item["path"])).resolve() == file),
            None,
        ) if isinstance(planned_compatibility, list) else None
        if anatomy_visible:
            if not isinstance(target, dict) or str(target.get("target_anatomy", "UNKNOWN")).upper() == "UNKNOWN" or str(target.get("anatomy_evidence_source", "UNKNOWN")).strip().upper() == "UNKNOWN":
                raise StylePackError(f"Attached anatomy-visible body reference has no concrete, evidence-backed target: {file}")
            if matching_compatibility is None:
                raise StylePackError(f"Attached anatomy-visible body reference has no per-file compatibility record: {file}")
        if matching_compatibility is not None:
            if str(matching_compatibility.get("status", "")).upper() != "ALLOWED":
                raise StylePackError(f"Attached body reference has no ALLOWED per-file compatibility decision: {file}")
            if isinstance(target, dict):
                enforce_reference_compatibility(target, {**body_row, "path": str(file)}, roles)
        # Pose-only attachment stays exempt from target anatomy metadata, after all manifest and hash checks.
        return

    if not isinstance(target, dict):
        return
    candidates: list[dict[str, object]] = []
    selected = plan.get("selected_references", {})
    if isinstance(selected, dict):
        for value in selected.values():
            candidates.extend(value if isinstance(value, list) else [value])
    auxiliary = plan.get("auxiliary_body_references", [])
    if isinstance(auxiliary, list):
        candidates.extend(auxiliary)
    record = next(
        (item for item in candidates if isinstance(item, dict) and item.get("path") and Path(str(item["path"])).resolve() == file),
        None,
    )
    if record is None:
        return
    planned_roles = {
        str(role).upper()
        for reference in planned_compatibility
        if isinstance(reference, dict)
        and reference.get("path")
        and Path(str(reference["path"])).resolve() == file
        for role in reference.get("roles", [])
    }
    if planned_roles:
        roles = sorted(planned_roles)
    metadata: dict[str, object] = dict(record)
    metadata.update(style_reference_compatibility_metadata(paths, file))
    metadata["path"] = str(file)
    enforce_reference_compatibility(target, metadata, roles)


def command_resolve_call(args: argparse.Namespace) -> None:
    paths = make_paths(args.workspace, args.style_name)
    request_id = safe_component(args.request_id, "request")
    plan_path = (paths.generations / "00_PENDING" / request_id / "REFERENCE_PLAN.json").resolve()
    assert_active_request_path(plan_path)
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise StylePackError(f"Cannot read prepared reference plan: {error}") from error
    if plan.get("request_id") != request_id:
        raise StylePackError("Reference plan belongs to another request.")
    workflow = plan.get("generation_workflow", {})
    stage_id = args.stage_id.strip().upper()
    if not stage_id:
        stage_id = str(workflow.get("stages", [{}])[0].get("stage_id", "")) if workflow.get("mode") == "MULTI_STAGE" else "SINGLE_PASS"
    stage_outputs = validated_stage_outputs(paths, plan_path, plan)
    slots = resolve_call_slots(paths, plan_path, plan, stage_id, stage_outputs)
    manifest = {
        "schema_version": 1,
        "request_id": request_id,
        "reference_plan": str(plan_path),
        "stage_id": stage_id,
        "confirmed_character_profile": plan.get("confirmed_character_profile"),
        "profile_default_application": plan.get("profile_default_application"),
        "profile_default_exceptions": plan.get("profile_default_exceptions"),
        "slots": [{key: slot[key] for key in ("path", "sha256", "active_roles", "slot", "physically_attach", "manifest_path") if key in slot} for slot in slots],
    }
    output_dir = plan_path.parent / "TECHNICAL_REFERENCES"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"RESOLVED_CALL_{safe_component(stage_id, 'stage')}.json"
    temporary = output_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, output_path)
    # This private snapshot is consumed only by generation_request's single
    # in-process resolve/assess/prepare invocation. Standalone CLI invocations
    # still resolve and validate their own current inputs.
    args._resolved_call_snapshot = {
        "request_id": request_id,
        "stage_id": stage_id,
        "reference_plan": str(plan_path),
        "plan_sha256": sha256(plan_path),
        "slots": slots,
        "stage_outputs": stage_outputs,
    }
    print(f"REFERENCE_MANIFEST={output_path}")
    for slot in manifest["slots"]:
        print(f"SLOT={slot['slot']} | SHA256={slot['sha256']} | ROLES={','.join(slot['active_roles'])} | PATH={slot['path']}")
    print("STATUS=CALL_REFERENCES_RESOLVED_FOR_RISK_ASSESSMENT")


def validate_reference_policy_slots(paths: StylePaths, plan: dict[str, object], call: dict[str, object], selections: dict[str, object], _seen: frozenset[str] = frozenset()) -> None:
    """Check reference consent against resolved attachment provenance."""
    policy = str(selections["reference_policy"]["choice"]).strip().upper()
    supported_policies = {
        "NO_REFERENCES",
        "BODY_LIBRARY_ONLY",
        "APPROVED_CHARACTER_REFERENCES",
        "APPROVED_PLUS_USER_REFERENCES",
        "USER_ATTACHED_REFERENCES",
        "PROJECT_STYLE_ONLY",
    }
    if policy not in supported_policies:
        raise StylePackError(f"Unsupported reference policy: {policy or '<empty>'}.")
    character = selections["character"]["choice"]
    slots = call.get("slots", [])
    identity_root = character_folder(paths, character) if re.fullmatch(r"CHAR_\d+", character) else None
    identity = [
        slot for slot in slots
        if identity_root and is_relative_to(Path(str(slot["path"])).resolve(), identity_root.resolve())
    ]
    if policy == "NO_REFERENCES":
        if identity_root:
            raise StylePackError("NO_REFERENCES conflicts with mandatory approved identity references for a named character.")
        if slots:
            raise StylePackError("NO_REFERENCES conflicts with attached slots.")
        return
    additional, library = [], []
    user_evidence = plan.get("user_reference_evidence", [])
    def is_user_attachment(slot: dict[str, object]) -> bool:
        for evidence in user_evidence:
            if not isinstance(evidence, dict) or not all(str(evidence.get(key, "")).strip() for key in ("path", "sha256", "source_path", "chat_id", "message_id")):
                continue
            source = Path(str(evidence["source_path"])).resolve()
            if any(is_relative_to(source, root) for root in (paths.pack, paths.generations, (paths.workspace / BODY_LIBRARY_NAME).resolve())):
                continue
            if (Path(str(evidence["path"])).resolve() == Path(str(slot["path"])).resolve()
                    and source.is_file() and sha256(source) == str(evidence["sha256"]).lower() == str(slot.get("sha256", "")).lower()
                    and evidence["chat_id"] == plan.get("startup_parameter_selection", {}).get("confirmed_provenance", {}).get("chat_id")):
                return True
        return False
    def inherits_reference_policy(slot: dict[str, object]) -> bool:
        plan_path = paths.generations / "00_PENDING" / str(plan.get("request_id", "")) / "REFERENCE_PLAN.json"
        outputs = validated_stage_outputs(paths, plan_path, plan)
        sources = [slot]
        if slot.get("manifest_path"):
            try:
                manifest = json.loads(Path(str(slot["manifest_path"])).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return False
            sources = manifest.get("sources", [])
            if not sources or manifest.get("request_id") != plan.get("request_id"):
                return False
        for source in sources:
            output = next((entry for entry in outputs.values() if entry["path"] == source.get("path") and entry["sha256"] == source.get("sha256")), None)
            if not output or output["path"] in _seen:
                return False
            snapshot_path = Path(output["plan_snapshot"])
            if sha256(snapshot_path) != output["plan_snapshot_sha256"]:
                return False
            snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
            if (
                str(snapshot.get("request_id", "")) != str(plan.get("request_id", ""))
                or str(snapshot.get("character_id", "")) != str(plan.get("character_id", ""))
                or snapshot.get("user_reference_evidence", []) != user_evidence
            ):
                return False
            try:
                validate_reference_policy_slots(paths, snapshot, snapshot.get("execution_call", {}), selections, _seen | {output["path"]})
            except StylePackError:
                return False
        return True
    if identity_root and not identity and not any(inherits_reference_policy(slot) for slot in slots):
        raise StylePackError("A named character has no attachment for mandatory approved identity assets or validated stage lineage.")

    def is_approved_project_reference(slot: dict[str, object]) -> bool:
        if is_user_attachment(slot):
            return False
        try:
            record = validate_plan_reference(paths, str(slot["path"]), "SCENE")
            status = str(record.get("status", ""))
            return (
                (status.startswith("APPROVED_") or status in {"POSITIVE_CANDIDATE", "WEB_EXPORT_COPY"})
                and record["sha256"] == str(slot.get("sha256", "")).lower()
            )
        except (StylePackError, OSError, ValueError):
            return False
    for slot in slots:
        file = Path(str(slot["path"])).resolve()
        roles = set(slot.get("active_roles", []))
        if identity_root and is_relative_to(file, identity_root):
            continue
        elif is_relative_to(file, (paths.workspace / BODY_LIBRARY_NAME).resolve()):
            library.append(slot)
        elif roles and roles.issubset({"STYLE", "STYLE_SOFT"}):
            continue  # Style consent is independently bound.
        else:
            additional.append(slot)
    if policy == "PROJECT_STYLE_ONLY":
        has_style = any(set(slot.get("active_roles", [])) & {"STYLE", "STYLE_SOFT"} for slot in slots)
        inherited = [slot for slot in additional if inherits_reference_policy(slot)]
        if selections["style"]["choice"] != f"PROJECT_STYLE:{paths.style_name}" or not has_style or len(inherited) != len(additional) or library:
            raise StylePackError("PROJECT_STYLE_ONLY permits only selected project STYLE and required character identity slots.")
        return
    if policy == "BODY_LIBRARY_ONLY":
        if str(plan.get("startup_parameter_selection", {}).get("resolved_parameters", {}).get("aux_body_decision", "")).upper() != "SELECTED":
            raise StylePackError("BODY_LIBRARY_ONLY requires the user's explicit request-level BODY_REFERENCE_LIBRARY selection.")
        inherited = [slot for slot in additional if inherits_reference_policy(slot)]
        if len(inherited) != len(additional):
            raise StylePackError("BODY_LIBRARY_ONLY permits the selected BODY_REFERENCE_LIBRARY and mandatory approved character identity slots only.")
        return
    if policy == "APPROVED_CHARACTER_REFERENCES":
        if library or not all(is_approved_project_reference(slot) or inherits_reference_policy(slot) for slot in additional):
            raise StylePackError("Approved-only reference policy conflicts with optional user/body-library reference slots.")
        return
    if policy == "APPROVED_PLUS_USER_REFERENCES" and not additional:
        raise StylePackError("Approved-plus-user reference policy requires an additional user-reference slot.")
    if policy in {"USER_ATTACHED_REFERENCES", "APPROVED_PLUS_USER_REFERENCES"}:
        if not additional or not all(is_user_attachment(slot) or inherits_reference_policy(slot) for slot in additional):
            raise StylePackError("User-reference policy requires actual non-style, non-library attachments with matching source hash and chat/message provenance.")


def command_prepare_call(args: argparse.Namespace) -> None:
    paths = make_paths(args.workspace, args.style_name)
    request_id = safe_component(args.request_id, "request")
    request_folder = paths.generations / "00_PENDING" / request_id
    plan_path = request_folder / "REFERENCE_PLAN.json"
    guard_path = request_folder / "EXECUTION_GUARD.json"
    try:
        guard_state = require_active_guard(guard_path, request_id)
    except ExecutionGuardError as error:
        raise StylePackError(f"Execution guard blocked call preparation: {error}") from error
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise StylePackError(f"Cannot read prepared reference plan: {error}") from error
    startup = plan.get("startup_parameter_selection", {})
    resolved = startup.get("resolved_user_selections", {})
    menu_selections = {
        key: {"choice": value, "user_quote": startup.get("resolved_selection_quote", startup.get("user_choice_quote", ""))}
        for key, value in resolved.items()
    }
    explicit_selections_supplied = bool(args.user_selections_json)
    if explicit_selections_supplied:
        try:
            user_selections = json.loads(Path(args.user_selections_json).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise StylePackError(f"Cannot read chat-sourced user selections: {error}") from error
        if menu_selections and user_selections != menu_selections:
            raise StylePackError("Explicit user selections differ from the complete resolved menu choices or exact menu reply; record a corrected selection before preparing the call.")
    elif menu_selections:
        user_selections = menu_selections
    else:
        raise StylePackError("This legacy plan has no complete menu selection mapping; show the structured chooser for missing choices before preparing the call.")
    if not isinstance(user_selections, dict) or any(
        not isinstance(user_selections.get(key), dict)
        or not isinstance(user_selections[key].get("choice"), str)
        or not user_selections[key]["choice"].strip()
        or not isinstance(user_selections[key].get("user_quote"), str)
        or not user_selections[key]["user_quote"].strip()
        for key in ("style", "reference_policy", "character")
    ):
        raise StylePackError(
            "User selections must record style, reference_policy, and character, each with choice and exact user_quote from this chat."
        )
    validate_menu_user_selections(
        plan.get("startup_parameter_selection", {}), user_selections,
        allow_legacy_explicit=explicit_selections_supplied,
    )
    recorded_selections = plan.get("user_selections")
    if isinstance(recorded_selections, dict) and recorded_selections != user_selections:
        raise StylePackError("User selections changed during this request; record the correction and prepare a new exact call.")
    if plan.get("request_id") != request_id or plan.get("gate_status") not in {
        "PREPARED_AWAITING_EXECUTABLE_CALL", "READY_FOR_GENERATION"
    }:
        raise StylePackError("The reference plan does not belong to this request or is not awaiting an executable call.")
    workflow = plan.get("generation_workflow", {})
    stage_id = args.stage_id.strip().upper()
    if not stage_id:
        if workflow.get("mode") == "MULTI_STAGE":
            stage_id = str(workflow.get("stages", [{}])[0].get("stage_id", ""))
        else:
            stage_id = "SINGLE_PASS"
    prompt_text = args.prompt_text
    if args.prompt_text_file:
        try:
            prompt_text = Path(args.prompt_text_file).read_text(encoding="utf-8")
        except OSError as error:
            raise StylePackError(f"Cannot read exact prompt text: {error}") from error
    if not prompt_text.strip():
        raise StylePackError("--prompt-text or --prompt-text-file must provide the exact call prompt.")
    prompt_text = render_confirmed_prompt(plan, prompt_text)
    original_plan_bytes = plan_path.read_bytes()
    original_plan_hash = hashlib.sha256(original_plan_bytes).hexdigest()
    ready_binding = guard_state.get("ready_binding")
    if guard_state.get("next_required_action") in {
        "CALL_VALIDATION_OR_EXECUTION_OR_BLOCKER",
        "EXECUTION_STARTED_OR_BLOCKER",
    }:
        stored_call = plan.get("execution_call")
        stored_prompt = stored_call.get("prompt", {}) if isinstance(stored_call, dict) else {}
        stored_risk = stored_call.get("risk_assessment", {}) if isinstance(stored_call, dict) else {}
        binding_matches = (
            isinstance(ready_binding, dict)
            and Path(str(ready_binding.get("path", ""))).resolve() == plan_path.resolve()
            and str(ready_binding.get("sha256", "")).lower() == original_plan_hash
            and str(ready_binding.get("stage", "")).upper() == stage_id.upper()
            and str(ready_binding.get("prompt_sha256", "")).lower()
            == hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()
        )
        same_candidate = (
            isinstance(stored_call, dict)
            and stored_call.get("request_id") == request_id
            and str(stored_call.get("stage_id", "")).upper() == stage_id.upper()
            and stored_prompt.get("text") == prompt_text
            and stored_prompt.get("text_sha256") == hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()
            and isinstance(stored_risk, dict)
            and Path(str(stored_risk.get("path", ""))).expanduser().resolve()
            == Path(args.risk_assessment).expanduser().resolve()
            and isinstance(stored_call.get("slots"), list)
            and stored_call.get("user_selections") == user_selections
        )
        if same_candidate and binding_matches:
            current_slots = resolve_call_slots(paths, plan_path.resolve(), plan, stage_id)
            current_slot_snapshot = [
                {key: slot[key] for key in ("path", "sha256", "active_roles", "slot", "physically_attach", "manifest_path") if key in slot}
                for slot in current_slots
            ]
            if current_slot_snapshot != stored_call.get("slots"):
                raise StylePackError("The READY call references changed since the exact executable call was bound.")
            _, current_report = load_and_validate_risk_assessment(
                str(stored_risk["path"]), prompt_text, stored_call["slots"]
            )
            stored_report = {key: value for key, value in stored_risk.items() if key != "path"}
            if current_report != stored_report:
                raise StylePackError("The READY risk report changed since the exact executable call was bound.")
            print(f"REFERENCE_PLAN={plan_path}")
            print(f"EXECUTION_CALL={json.dumps(stored_call, ensure_ascii=False, separators=(',', ':'))}")
            print(f"GENERATION_RISK={current_report['generation_risk']}")
            print("STATUS=READY_FOR_GENERATION (unchanged retry; existing guard binding preserved)")
            return
        raise StylePackError(
            "The request already has a READY executable call. Its prompt, slots, hashes, roles, and risk snapshot are immutable; "
            "start that call or reconcile the guard before preparing another one."
        )
    resolved_snapshot = getattr(args, "_resolved_call_snapshot", None)
    reuse_resolved_slots = (
        isinstance(resolved_snapshot, dict)
        and str(resolved_snapshot.get("request_id", "")) == request_id
        and str(resolved_snapshot.get("stage_id", "")).upper() == stage_id.upper()
        and Path(str(resolved_snapshot.get("reference_plan", ""))).resolve() == plan_path.resolve()
        and str(resolved_snapshot.get("plan_sha256", "")).lower() == original_plan_hash
        and isinstance(resolved_snapshot.get("slots"), list)
        and isinstance(resolved_snapshot.get("stage_outputs"), dict)
    )
    if isinstance(resolved_snapshot, dict) and not reuse_resolved_slots:
        raise StylePackError("Internal resolved call snapshot no longer matches the current request plan and stage.")
    call, risk_path, report = resolve_execution_call(
        paths,
        plan_path.resolve(),
        plan,
        stage_id,
        prompt_text,
        args.risk_assessment,
        resolved_slots=resolved_snapshot["slots"] if reuse_resolved_slots else None,
        resolved_stage_outputs=resolved_snapshot["stage_outputs"] if reuse_resolved_slots else None,
    )
    call["user_selections"] = user_selections
    scene_prompt_sources = plan.get("scene_prompt_sources", {})
    if scene_prompt_sources:
        call["prompt_source_metadata"] = {
            "source": "EXACT_EXECUTABLE_PROMPT",
            "prompt_sha256": call["prompt"]["text_sha256"],
            "roles": sorted(str(role).upper() for role in scene_prompt_sources),
            "role_sources": scene_prompt_sources,
        }
    if args.user_reference_evidence_json:
        try:
            evidence = json.loads(Path(args.user_reference_evidence_json).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise StylePackError(f"Cannot read user attachment provenance: {error}") from error
        if not isinstance(evidence, list):
            raise StylePackError("User attachment provenance must be a list.")
        plan["user_reference_evidence"] = evidence
    validate_reference_policy_slots(paths, plan, call, user_selections)
    plan["user_selections"] = user_selections
    plan["execution_call"] = call
    plan["risk_assessment"] = {
        "path": str(risk_path),
        **report,
        "prompt": {"text": prompt_text, "text_sha256": call["prompt"]["text_sha256"]},
    }
    plan["gate_status"] = "READY_FOR_GENERATION"
    serialized_plan = (json.dumps(plan, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    temporary = plan_path.with_suffix(".REFERENCE_PLAN.tmp")
    try:
        temporary.write_bytes(serialized_plan)
        os.replace(temporary, plan_path)
    except OSError as error:
        raise StylePackError(f"Could not persist executable call snapshot atomically: {error}") from error
    try:
        execution_checkpoint(
            guard_path,
            event="READY_FOR_EXECUTION",
            summary=f"Exact executable call prepared for {stage_id}; prompt, slot bytes, roles, and risk report are bound.",
            reference_plan=str(plan_path.resolve()),
            execution_call=call,
            stage=stage_id,
        )
    except ExecutionGuardError as error:
        current_plan_hash = sha256(plan_path)
        try:
            current_guard = load_execution_guard(guard_path)
        except (ExecutionGuardError, OSError, ValueError):
            current_guard = {}
        committed_binding = current_guard.get("ready_binding")
        if (
            isinstance(committed_binding, dict)
            and Path(str(committed_binding.get("path", ""))).resolve() == plan_path.resolve()
            and str(committed_binding.get("sha256", "")).lower() == current_plan_hash
            and str(committed_binding.get("stage", "")).upper() == stage_id.upper()
            and str(committed_binding.get("prompt_sha256", "")).lower() == call["prompt"]["text_sha256"]
        ):
            pass  # The guard persisted before the exception surfaced; the snapshot is already READY.
        else:
            current_action = str(current_guard.get("next_required_action", ""))
            if current_action != "CALL_VALIDATION_OR_EXECUTION_OR_BLOCKER":
                rollback = plan_path.with_suffix(".REFERENCE_PLAN.rollback.tmp")
                try:
                    rollback.write_bytes(original_plan_bytes)
                    os.replace(rollback, plan_path)
                except OSError as rollback_error:
                    raise StylePackError(
                        f"Guard readiness failed ({error}) and the original plan could not be restored ({rollback_error}); "
                        f"inspect {plan_path} and {guard_path}."
                    ) from rollback_error
            raise StylePackError(f"Call snapshot was saved, but guard readiness checkpoint failed: {error}") from error
    print(f"REFERENCE_PLAN={plan_path}")
    print(f"EXECUTION_CALL={json.dumps(call, ensure_ascii=False, separators=(',', ':'))}")
    print(f"GENERATION_RISK={report['generation_risk']}")
    print(f"STATUS=READY_FOR_GENERATION")


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


def preparation_args_signature(args: argparse.Namespace) -> str:
    """Fingerprint parsed preparation input for safe in-process reuse."""
    values = {key: value for key, value in vars(args).items() if not key.startswith("_") and key != "handler"}
    payload = json.dumps(values, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def reusable_preparation_semantics(args: argparse.Namespace) -> dict[str, object] | None:
    snapshot = getattr(args, "_preparation_semantic_snapshot", None)
    if isinstance(snapshot, dict) and snapshot.get("signature") == preparation_args_signature(args):
        return snapshot
    return None


def parse_selected_source_reviews(
    values: Sequence[str],
    selected: dict[str, object],
    active_roles_by_key: dict[str, list[str]],
    auxiliary: Sequence[dict[str, object]] = (),
) -> list[dict[str, object]]:
    """Bind visual reviews to each semantic role and physical attachment slot."""
    expected: dict[tuple[str, str, str], dict[str, object]] = {}
    for key, value in selected.items():
        roles = active_roles_by_key.get(key, [])
        if not roles:
            continue
        records = value if isinstance(value, list) else [value]
        for record in records:
            if not isinstance(record, dict) or not record.get("path"):
                continue
            path = Path(str(record["path"])).resolve()
            for role in roles:
                review_role = "SUBJECT" if role.upper() == "POSE_SOFT" else role.upper()
                slot_role = key.upper()
                expected[(review_role, slot_role, str(path).casefold())] = {
                    "role": review_role,
                    "active_slot_roles": [slot_role],
                    "path": str(path),
                    "sha256": str(record.get("sha256") or sha256(path)).lower(),
                }
    for record in auxiliary:
        path = Path(str(record["path"])).resolve()
        auxiliary_review_roles = {
            "AUX_BODY_BUILD": ("BODY",),
            "AUX_POSE": ("POSE",),
            "AUX_CAMERA": ("COMPOSITION",),
            "AUX_CLOTHING_BEHAVIOR": ("CLOTHES",),
            "AUX_OBJECT_INTERACTION": ("BODY", "COMPOSITION"),
        }
        for role in record["active_roles"]:
            review_roles = auxiliary_review_roles.get(str(role).upper())
            if not review_roles:
                raise StylePackError(f"No visual-review category is defined for auxiliary role {role}.")
            slot_role = f"{str(record['ref_id']).upper()}:{str(role).upper()}"
            for review_role in review_roles:
                expected[(review_role, slot_role, str(path).casefold())] = {
                    "role": review_role,
                    "active_slot_roles": [slot_role],
                    "path": str(path),
                    "sha256": str(record.get("sha256") or sha256(path)).lower(),
                }

    reviewed: dict[tuple[str, str, str], dict[str, object]] = {}
    for value in values:
        try:
            declaration = json.loads(value)
        except (TypeError, json.JSONDecodeError) as exc:
            raise StylePackError("--reviewed-source must be a JSON visual-review attestation with role, slot_role, path, view, outcome, applicability, findings, and limitations.") from exc
        if not isinstance(declaration, dict):
            raise StylePackError("--reviewed-source must be a JSON object containing the visual-review attestation fields.")
        required_fields = ("role", "slot_role", "path", "view", "outcome", "applicability", "findings", "limitations")
        absent_fields = [name for name in required_fields if not str(declaration.get(name) or "").strip()]
        if absent_fields:
            raise StylePackError("--reviewed-source attestation is missing required fields: " + ", ".join(absent_fields))
        role = str(declaration["role"]).strip().upper()
        slot_role = str(declaration["slot_role"]).strip().upper()
        view = str(declaration["view"]).strip().upper()
        outcome = str(declaration["outcome"]).strip().upper()
        if view != "FULL_RESOLUTION":
            raise StylePackError("Selected-source review must declare view=FULL_RESOLUTION.")
        if outcome != "PASS":
            raise StylePackError(f"Selected-source review outcome is {outcome}; only PASS can prepare generation.")
        if role not in REVIEW_CATEGORIES:
            raise StylePackError(f"Unknown reviewed source role {role}; choose from {', '.join(REVIEW_CATEGORIES)}.")
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*(?::[A-Z][A-Z0-9_]*)?", slot_role):
            raise StylePackError(f"Invalid physical slot role {slot_role!r}; use the exact active role from the attachment plan.")
        path = Path(str(declaration["path"]).strip()).expanduser().resolve()
        key = (role, slot_role, str(path).casefold())
        if key not in expected:
            raise StylePackError(f"Reviewed source is not an exact selected source for role {role} and slot {slot_role}: {path}")
        if not path.is_file():
            raise StylePackError(f"Reviewed selected source is missing: {path}")
        expected_record = expected[key]
        actual_hash = sha256(path).lower()
        if actual_hash != expected_record["sha256"]:
            raise StylePackError(f"Selected source changed after planning for role {role}: {path}")
        reviewed[key] = {
            **expected_record,
            "view": view,
            "applicability": str(declaration["applicability"]).strip(),
            "review_findings": str(declaration["findings"]).strip(),
            "limitations": str(declaration["limitations"]).strip(),
            "review_outcome": outcome,
            "review_basis": "CALLER_DECLARED_CURRENT_REQUEST_VISUAL_REVIEW",
        }

    missing = [expected_record for key, expected_record in expected.items() if key not in reviewed]
    if missing:
        labels = [f"{item['role']}@{item['active_slot_roles'][0]}={item['path']} (sha256 {item['sha256']})" for item in missing]
        raise StylePackError("Full-resolution visual review is required for every attached source, semantic role, and slot role: " + "; ".join(labels))
    return sorted(reviewed.values(), key=lambda item: (str(item["role"]), str(item["path"]).casefold()))


def active_roles_by_selection_key(existing_scene: bool, character_free_scene: bool) -> dict[str, list[str]]:
    """Map each selected plan key to an existing semantic full-resolution review category."""
    return {
        "style": ["STYLE"],
        "subject": ["POSE_SOFT"],
        "character_assembly": ["FACE", "BODY"],
        "primary_face": ["FACE"] if existing_scene else ["STYLE"],
        "supporting_face": ["STYLE"],
        "expression": ["FACE"] if existing_scene else ["STYLE"],
        "body": ["BODY"] if not character_free_scene else ["POSE_SOFT"],
        "pose": ["POSE"],
        "clothes": ["CLOTHES"],
        "accessory": ["CLOTHES"],
        "face_variant": ["FACE"],
        "body_variant": ["BODY"],
        "lighting": ["LIGHTING"],
        "background": ["BACKGROUND"],
        "composition": ["COMPOSITION"],
        "coverage_front": ["CLOTHES"],
        "coverage_side": ["CLOTHES"],
        "coverage_back": ["CLOTHES"],
    }


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


def style_name_is_explicitly_mentioned(style_name: str, quote: str) -> bool:
    normalized_style = re.sub(r"[^a-z0-9]+", "", style_name.casefold())
    normalized_quote = re.sub(r"[^a-z0-9]+", "", quote.casefold())
    return bool(normalized_style and normalized_style in normalized_quote)


def has_positive_master_style_manifest_entry(paths: StylePaths, relative: Path) -> bool:
    if tuple(relative.parts[:2]) != ("01_WORK", "STYLE_CROPS") or len(relative.parts) != 3:
        return False
    if not relative.name.upper().startswith("MASTER_STYLE_"):
        return False
    stored_path = relative.as_posix()
    matches = [
        row for row in read_csv(paths.references)
        if row.get("stored_relative_path", "").replace("\\", "/") == stored_path
    ]
    return (
        len(matches) == 1
        and matches[0].get("primary_role", "").upper() == "MASTER_STYLE"
        and matches[0].get("status", "").upper() == "TEST"
        and matches[0].get("generator_safe", "").upper() == "YES"
    )


def validate_plan_reference(
    paths: StylePaths,
    value: str,
    label: str,
    *,
    allow_curated_body_contour: bool = False,
    allow_request_local_style_candidate: bool = False,
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
    request_local_style_candidate = False
    sketch_style_support: dict[str, object] = {}
    if is_curated_body_contour:
        status = "FINAL_CURATED_BODY_CONTOUR"
        roles = ["POSE", "BODY_CONTOUR"]
    elif is_relative_to(file, paths.pack):
        relative = file.relative_to(paths.pack)
        status, _ = local_asset_status(relative, file.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS)
        roles = inferred_asset_roles(relative)
        if status in {"REJECTED", "REVIEW_ONLY", "DERIVED_GENERATION"}:
            raise StylePackError(f"{label} cannot use {status} as a positive reference: {file}")
        request_local_style_candidate = (
            allow_request_local_style_candidate
            and label == "STYLE"
            and "STYLE" in roles
            and status == "POSITIVE_CANDIDATE"
            and has_positive_master_style_manifest_entry(paths, relative)
        )
        if style_formation_status(paths) != "FORMED" and not request_local_style_candidate:
            raise StylePackError(
                f"{label} cannot use an unformed style-pack source as an explicit planning reference; use a registered successful QA-passed generation."
            )
        if request_local_style_candidate:
            status = "REQUEST_LOCAL_STYLE_CANDIDATE"
    else:
        generation = registered_approved_generation(paths, file)
        if generation and str(generation.get("status", "")).upper() == "APPROVED_SKETCH":
            generation = None  # Sketches must pass the exact role catalog and style-only restrictions.
        if generation is None:
            character_asset = registered_approved_character_asset(paths, file)
            if character_asset is None:
                raise StylePackError(
                    f"{label} cannot use an unregistered, rejected, staging, or non-approved generation as a positive reference: {file}"
                )
            if character_asset.get("asset_role") == "SKETCH":
                if label != "STYLE":
                    raise StylePackError(f"{label} cannot use a useful sketch as identity, anatomy, clothing, or scene ground truth: {file}")
                roles = ["STYLE", "SKETCH"]
                sketch_style_support = {
                    "asset_role": "SKETCH", "character_id": character_asset["character_id"],
                    "usage": "STYLE_SUPPORT_ONLY", "purpose": character_asset.get("purpose", ""),
                    "reference_scope": "CURRENT_REQUEST_ONLY", "permanent_anchor": False,
                    "canonical_identity_priority": True,
                }
            status = "APPROVED_CHARACTER_ASSET"
        else:
            status = str(generation["status"])
    compatibility = style_reference_compatibility_metadata(paths, file)
    return {
        "path": str(file),
        "sha256": sha256(file),
        "status": status,
        "inferred_roles": roles,
        **({"reference_scope": "CURRENT_REQUEST_ONLY", "permanent_anchor": False} if request_local_style_candidate else {}),
        **sketch_style_support,
        **compatibility,
    }


def approved_character_role_assets(
    paths: StylePaths, character_id: str, profile: Path
) -> tuple[list[dict[str, str]], dict[str, object]]:
    """Merge exact approved role records with profile indexes without trusting folder presence."""
    character_id = character_id.strip().upper()
    profile_fields = {
        "APPROVED_WARDROBE": ("wardrobe_references", "WARDROBE", "03_CHARACTER_REFERENCES/03_WARDROBE"),
        "APPROVED_ACCESSORY": ("accessory_references", "ACCESSORY", "03_CHARACTER_REFERENCES/04_ACCESSORIES"),
        "APPROVED_FACE_VARIANT": ("face_variant_references", "FACE_VARIANT", "03_CHARACTER_REFERENCES/05_FACE_VARIANTS"),
        "APPROVED_BODY_VARIANT": ("body_variant_references", "BODY_VARIANT", "03_CHARACTER_REFERENCES/06_BODY_VARIANTS"),
        "APPROVED_SKETCH": ("useful_sketches", "SKETCH", "03_CHARACTER_REFERENCES/07_USEFUL_SKETCHES"),
    }
    profile_index = {role: [] if role == "SKETCH" else _character_profile_paths(profile, field) for field, role, _ in profile_fields.values()}
    approved: dict[str, dict[str, str]] = {}
    diagnostics: list[dict[str, str]] = []
    manifest_rows = read_csv(paths.generation_manifest)

    registry_rows = [
        row for row in read_csv(paths.character_registry)
        if row.get("character_id", "").strip().upper() == character_id
        and row.get("status", "").strip().upper() == "APPROVED"
        and (
            (Path(row.get("profile_path", "")) if Path(row.get("profile_path", "")).is_absolute() else paths.generations / row.get("profile_path", "")).resolve()
            == profile.resolve()
        )
    ]
    base_manifest: dict[str, str] | None = None
    if len(registry_rows) == 1:
        registry = registry_rows[0]
        base = Path(registry.get("approved_base", ""))
        if not base.is_absolute():
            base = paths.generations / base
        base = base.resolve()
        try:
            _approved_profile_identity(paths, profile, character_id)
            profile_valid = True
        except (OSError, StylePackError, ValueError):
            profile_valid = False
        if profile_valid and base.is_file():
            base_manifest = newest_matching_generation(paths, base)
        if not (
            base_manifest
            and base_manifest.get("status", "").upper() == "APPROVED_CHARACTER_BASE"
            and base_manifest.get("character_id", "").upper() == character_id
            and Path(base_manifest.get("style_file", "")).resolve() == base
            and generation_has_passed_qa(base_manifest)
        ):
            base_manifest = None

    # Initial clothing/accessory references are approved as part of the canonical
    # base. They remain role-specific and are admitted only through the exact
    # approved profile and its still-valid canonical base record.
    if base_manifest:
        for field, role, expected_folder in profile_fields.values():
            if role not in {"WARDROBE", "ACCESSORY"}:
                continue
            expected_root = (character_folder(paths, character_id) / expected_folder).resolve()
            for listed in profile_index[role]:
                listed_manifest = newest_matching_generation(paths, listed) if listed.is_file() else None
                if listed_manifest:
                    expected_status = "APPROVED_WARDROBE" if role == "WARDROBE" else "APPROVED_ACCESSORY"
                    if (
                        listed_manifest.get("status", "").upper() != expected_status
                        or listed_manifest.get("character_id", "").upper() != character_id
                        or Path(listed_manifest.get("style_file", "")).resolve() != listed.resolve()
                    ):
                        diagnostics.append({
                            "generation_id": listed_manifest.get("generation_id", ""),
                            "status": listed_manifest.get("status", ""),
                            "path": str(listed),
                            "role": role,
                            "reason": "A later per-file manifest record revokes the initial approved-base fallback for this profile-listed asset.",
                        })
                    # A per-file record supersedes base-only approval. It is
                    # admitted only by the dedicated approved-role path below.
                    continue
                if not listed.is_file() or not is_relative_to(listed, expected_root):
                    diagnostics.append({
                        "generation_id": base_manifest.get("generation_id", ""),
                        "status": "APPROVED_CHARACTER_BASE",
                        "path": str(listed),
                        "role": role,
                        "reason": "Profile-listed base asset is missing or outside its exact role directory.",
                    })
                    continue
                try:
                    asset_hash = sha256(listed)
                except OSError as error:
                    diagnostics.append({
                        "generation_id": base_manifest.get("generation_id", ""),
                        "status": "APPROVED_CHARACTER_BASE",
                        "path": str(listed),
                        "role": role,
                        "reason": f"Approved profile-listed base asset could not be verified: {error}",
                    })
                    continue
                approved[str(listed)] = {
                    "role": role,
                    "path": str(listed),
                    "generation_id": base_manifest.get("generation_id", ""),
                    "status": "APPROVED_CHARACTER_BASE",
                    "description": base_manifest.get("description", ""),
                    "sha256": asset_hash,
                    "profile_indexed": "true",
                    "provenance": base_manifest.get("notes", ""),
                }

    for row in manifest_rows:
        status = row.get("status", "").strip().upper()
        if row.get("character_id", "").strip().upper() != character_id or status not in profile_fields:
            continue
        field, role, expected_folder = profile_fields[status]
        file = Path(row.get("style_file", ""))
        if not file.is_absolute():
            file = paths.workspace / file
        file = file.resolve()
        expected_root = (character_folder(paths, character_id) / expected_folder).resolve()
        reason = ""
        if not is_relative_to(file, expected_root):
            reason = "Approved manifest path is outside its exact role directory."
        elif not file.is_file():
            reason = "Approved manifest asset file is missing."
        manifest = None
        verification_error = ""
        if file.is_file():
            try:
                manifest = newest_matching_generation(paths, file)
            except (OSError, RuntimeError, ValueError) as error:
                verification_error = str(error)
        if not reason and not manifest:
            reason = f"Latest-generation verification failed: {verification_error}" if verification_error else "No latest generation manifest record verifies this exact file."
        elif not reason and manifest and manifest.get("status", "").upper() != status:
            reason = f"Latest record status is {manifest.get('status', '')!r}, not {status}."
        elif not reason and manifest and manifest.get("character_id", "").upper() != character_id:
            reason = "Latest record belongs to a different character."
        elif not reason and manifest and Path(manifest.get("style_file", "")).resolve() != file:
            reason = "Latest record does not bind this exact approved path."
        elif not reason and manifest and manifest.get("qa_output_sha256", "").strip() and manifest.get("qa_output_sha256", "").casefold() != sha256(file).casefold():
            reason = "Manifest QA output hash does not match the current approved asset file."
        elif not reason and manifest and not generation_has_passed_qa(manifest):
            reason = "No valid manager-approved QA receipt/provenance verifies this approved role asset."
        if reason:
            diagnostics.append({
                "generation_id": row.get("generation_id", ""),
                "status": status,
                "path": str(file),
                "role": role,
                "reason": reason,
            })
            continue
        assert manifest is not None
        try:
            asset_hash = sha256(file)
        except OSError as error:
            diagnostics.append({
                "generation_id": row.get("generation_id", ""),
                "status": status,
                "path": str(file),
                "role": role,
                "reason": f"Approved asset hash verification failed: {error}",
            })
            continue
        approved[str(file)] = {
            "role": role,
            "path": str(file),
            "generation_id": manifest.get("generation_id", ""),
            "status": status,
            "description": manifest.get("description", ""),
            "sha256": asset_hash,
            "profile_indexed": str(file in profile_index[role]).lower(),
            "provenance": manifest.get("notes", ""),
        }
        if role == "SKETCH":
            approved[str(file)].update({"label": "Useful unfinished character sketch",
                                       "purpose": manifest.get("description", ""), "usage": "STYLE_SUPPORT_ONLY"})
    stale = {
        role: sorted(str(path) for path in paths_for_role if str(path) not in approved)
        for role, paths_for_role in profile_index.items()
        if any(str(path) not in approved for path in paths_for_role)
    }
    missing_index = {
        role: sorted(item["path"] for item in approved.values() if item["role"] == role and item["path"] not in {str(p) for p in profile_index[role]})
        for role in profile_index if role != "SKETCH"
        if any(item["role"] == role and item["path"] not in {str(p) for p in profile_index[role]} for item in approved.values())
    }
    unsupported = sorted({
        row.get("status", "").strip().upper()
        for row in manifest_rows
        if row.get("character_id", "").strip().upper() == character_id
        and row.get("status", "").strip().upper().startswith("APPROVED_")
        and row.get("status", "").strip().upper() not in profile_fields
        and row.get("status", "").strip().upper() != "APPROVED_CHARACTER_BASE"
    })
    return sorted(approved.values(), key=lambda item: (item["role"], item["path"].casefold())), {
        "stale_profile_entries": stale,
        "approved_records_missing_from_profile": missing_index,
        "unsupported_approved_statuses": unsupported,
        "invalid_approved_records": diagnostics,
    }


def registered_approved_character_asset(paths: StylePaths, image: Path) -> dict[str, str] | None:
    """Recognize exact approved identity and role assets; never infer approval from a directory."""
    candidate = image.resolve()
    for row in read_csv(paths.character_registry):
        character_id = row.get("character_id", "").strip().upper()
        if not character_id or row.get("status", "").upper() != "APPROVED":
            continue
        profile = Path(row.get("profile_path", ""))
        if not profile.is_absolute():
            profile = paths.generations / profile
        try:
            identity = _approved_profile_identity(paths, profile, character_id)
        except (OSError, StylePackError, ValueError):
            continue

        base = Path(row.get("approved_base", ""))
        if not base.is_absolute():
            base = paths.generations / base
        manifest = newest_matching_generation(paths, base) if base.is_file() else None
        if not (
            manifest
            and manifest.get("status", "").upper() == "APPROVED_CHARACTER_BASE"
            and manifest.get("character_id", "").upper() == character_id
            and manifest.get("style_file", "")
            and Path(manifest["style_file"]).resolve() == base.resolve()
        ):
            continue

        if candidate == base.resolve():
            return {"character_id": character_id, "asset_role": "BASE", **identity}

        for field, profile_key, role in (
            ("face_references", "character_face_references", "FACE"),
            ("body_references", "character_body_references", "BODY"),
        ):
            registry_paths = _character_registry_paths(paths, row.get(field, ""))
            profile_paths = _character_profile_paths(profile, profile_key)
            if candidate in registry_paths and candidate in profile_paths:
                return {"character_id": character_id, "asset_role": role, **identity}
        role_assets, _ = approved_character_role_assets(paths, character_id, profile)
        for asset in role_assets:
            if candidate == Path(asset["path"]).resolve():
                return {"character_id": character_id, "asset_role": asset["role"], **asset, **identity}
    return None


def require_approved_character_asset_role(
    paths: StylePaths, image: Path, character_id: str, expected_role: str, label: str
) -> dict[str, str]:
    """Bind canonical scene slots to the exact registry role, excluding optional variants."""
    registered = registered_approved_character_asset(paths, image)
    if (
        not registered
        or registered.get("character_id", "").upper() != character_id.upper()
        or registered.get("asset_role", "").upper() != expected_role.upper()
    ):
        actual = (registered or {}).get("asset_role", "UNREGISTERED")
        raise StylePackError(
            f"{label} requires the selected character's exact approved {expected_role} asset; found {actual}. Optional face/body variants cannot fill canonical identity slots."
        )
    return registered


def _character_registry_paths(paths: StylePaths, value: str) -> set[Path]:
    result: set[Path] = set()
    for item in value.split(";"):
        item = item.strip()
        if item:
            path = Path(item)
            result.add((path if path.is_absolute() else paths.generations / path).resolve())
    return result


def _character_profile_paths(profile: Path, key: str) -> set[Path]:
    """Read the generated YAML list for one identity-reference field."""
    if (profile.parent / "CONFIRMED_PROFILE" / "ACTIVE.json").is_file():
        values = load_effective(profile)["profile"].get(key, []) or []
        return {(profile.parent / str(value)).resolve() for value in values}
    try:
        lines = profile.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeError):
        return set()
    values: set[Path] = set()
    in_list = False
    for line in lines:
        if not in_list:
            if line.strip() == f"{key}:":
                in_list = True
            continue
        if line and not line[0].isspace():
            break
        match = re.fullmatch(r'\s+-\s+["\']?(.+?)["\']?\s*', line)
        if match:
            values.add((profile.parent / match.group(1)).resolve())
    return values


def sync_character_profile_asset(profile: Path, field: str, asset: Path) -> None:
    """Idempotently add one manager-approved asset to its distinct profile role list."""
    try:
        content = profile.read_text(encoding="utf-8-sig")
        relative = asset.resolve().relative_to(profile.parent.resolve()).as_posix()
    except (OSError, ValueError) as error:
        raise StylePackError(f"Approved asset was recorded, but profile synchronization could not resolve/read its target: {error}") from error
    quoted = yaml_quote(relative)
    lines = content.splitlines()
    start = next((index for index, line in enumerate(lines) if line.strip().startswith(field + ":")), None)
    if start is None:
        lines.extend([f"{field}:", f"  - {quoted}"])
    else:
        inline = re.fullmatch(rf"(\s*{re.escape(field)}:\s*)\[(.*?)\](\s*(?:#.*)?)", lines[start])
        if inline:
            raw_items = [item.strip() for item in inline.group(2).split(",") if item.strip()]
            for item in raw_items:
                value = item[1:-1] if len(item) >= 2 and item[0] == item[-1] and item[0] in {"'", '"'} else item
                if (profile.parent / value).resolve() == asset.resolve():
                    return
            lines[start] = f"{field}:{inline.group(3)}"
            lines[start + 1:start + 1] = [f"  - {item}" for item in raw_items] + [f"  - {quoted}"]
            start = -1
        if lines[start].strip() == f"{field}: []":
            lines[start] = f"{field}:"
            lines.insert(start + 1, f"  - {quoted}")
            start = -1
        if start == -1:
            end = -1
        else:
            end = start + 1
            while end < len(lines) and (not lines[end] or lines[end][0].isspace()):
                end += 1
            entries = lines[start + 1:end]
            if any((profile.parent / match.group(1)).resolve() == asset.resolve() for line in entries if (match := re.fullmatch(r"\s+-\s+[\"']?(.+?)[\"']?\s*", line))):
                return
            lines.insert(end, f"  - {quoted}")
    temporary = profile.with_suffix(profile.suffix + ".sync.tmp")
    try:
        temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
        temporary.replace(profile)
    except OSError as error:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise StylePackError(f"Approved asset was recorded, but profile synchronization failed: {error}") from error


def style_reference_compatibility_metadata(paths: StylePaths, file: Path) -> dict[str, str]:
    source_character_id = canonical_source_character_id(paths, file)
    anatomy_compatibility = "UNKNOWN"
    anatomy_evidence_source = "UNKNOWN"
    framing = "UNKNOWN"
    coverage = "UNKNOWN"
    source_coverage = "UNKNOWN"
    shot_type = "UNKNOWN"
    if is_relative_to(file, paths.pack) and paths.references.is_file():
        relative = file.relative_to(paths.pack).as_posix()
        for row in read_csv(paths.references):
            if row.get("stored_relative_path", "").replace("\\", "/") == relative:
                if row.get("status", "").upper() in {"APPROVED", "ANCHOR"} and row.get("user_approved", "").upper() == "YES":
                    anatomy_compatibility = row.get("subject_anatomy_compatibility") or row.get("anatomy_compatibility", "UNKNOWN") or "UNKNOWN"
                    anatomy_evidence_source = row.get("subject_anatomy_evidence_source") or row.get("anatomy_evidence_source", "UNKNOWN") or "UNKNOWN"
                    framing = row.get("framing", "UNKNOWN") or "UNKNOWN"
                    coverage = row.get("coverage", "UNKNOWN") or "UNKNOWN"
                    source_coverage = row.get("source_coverage", "UNKNOWN") or "UNKNOWN"
                    shot_type = row.get("shot_type", "UNKNOWN") or "UNKNOWN"
                break
    return {
        "source_character_id": source_character_id,
        "anatomy_compatibility": anatomy_compatibility,
        "anatomy_evidence_source": anatomy_evidence_source,
        "framing": framing,
        "coverage": coverage,
        "source_coverage": source_coverage,
        "shot_type": shot_type,
    }


def generation_row_matches_image(row: dict[str, str], image: Path) -> bool:
    """Match a manifest row to an image by content hash across existing copies."""
    image_hash = sha256(image)
    for field in ("source_image", "archive_file", "style_file"):
        candidate_text = row.get(field, "")
        if not candidate_text:
            continue
        candidate = Path(candidate_text)
        if not generation_candidate_accessible(row, candidate):
            continue
        if candidate.is_file() and sha256(candidate) == image_hash:
            return True
    return False


def generation_row_hashes(row: dict[str, str]) -> set[str]:
    hashes: set[str] = set()
    for field in ("source_image", "archive_file", "style_file"):
        candidate = Path(row.get(field, ""))
        if not generation_candidate_accessible(row, candidate):
            continue
        if candidate.is_file():
            hashes.add(sha256(candidate))
    return hashes


def generation_candidate_accessible(row: dict[str, str], candidate: Path) -> bool:
    """Never open historical unapproved copies while matching manifest rows."""
    if not os.environ.get("CODEX_THREAD_ID", "").strip():
        return True  # Offline manager/test operation has no chat scope.
    parts = {part.upper() for part in candidate.parts}
    approved_folder = bool(parts & {"01_APPROVED_CHARACTERS", "02_APPROVED_STANDALONE"})
    forbidden_folder = bool(parts & {"00_PENDING", "REJECTED", "DRAFT", "STAGING", "TEST", "GENERATION_RESULTS"})
    if str(row.get("status", "")).upper().startswith("APPROVED") and approved_folder and not forbidden_folder:
        return True
    try:
        assert_active_request_path(candidate)
    except StylePackError:
        return False
    return True


def newest_matching_generation(paths: StylePaths, image: Path) -> dict[str, str] | None:
    """Resolve the one authoritative (latest) manifest status for an image."""
    target_stat = image.stat()
    target_hash = sha256(image)
    def fingerprint(path: Path, stat_result: os.stat_result) -> tuple[str, int, int, int, int]:
        return (
            str(path.resolve()),
            stat_result.st_size,
            stat_result.st_mtime_ns,
            stat_result.st_ctime_ns,
            getattr(stat_result, "st_ino", 0),
        )

    hash_cache: dict[tuple[str, int, int, int, int], str] = {
        fingerprint(image, target_stat): target_hash,
    }
    for row in reversed(read_csv(paths.generation_manifest)):
        # A recorded rejection remains authoritative without reading its files.
        recorded_hash = str(row.get("qa_output_sha256", "")).casefold()
        if recorded_hash and recorded_hash == target_hash:
            return row
        for field in ("source_image", "archive_file", "style_file"):
            candidate_text = row.get(field, "")
            if not candidate_text:
                continue
            candidate = Path(candidate_text)
            if not generation_candidate_accessible(row, candidate):
                continue
            try:
                stat_result = candidate.stat()
            except OSError:
                continue
            if not stat_module.S_ISREG(stat_result.st_mode) or stat_result.st_size != target_stat.st_size:
                continue
            key = fingerprint(candidate, stat_result)
            candidate_hash = hash_cache.get(key)
            if candidate_hash is None:
                candidate_hash = sha256(candidate)
                hash_cache[key] = candidate_hash
            if candidate_hash == target_hash:
                return row
    return None


def generation_qa_evidence(row: dict[str, str]) -> dict[str, object] | None:
    """Validate the durable record-generation receipt and its QA contract snapshot."""
    evidence_path = Path(row.get("qa_evidence", ""))
    style_file = Path(row.get("style_file", ""))
    if not generation_candidate_accessible(row, style_file):
        return None
    expected_evidence = style_file.with_suffix(style_file.suffix + ".qa-evidence.json")
    if not style_file.is_file() or evidence_path != expected_evidence or not evidence_path.is_file():
        return None
    try:
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(evidence, dict) or evidence.get("schema_version") not in {2, 3}:
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
    # Preserve previously valid manager-issued receipts. The new visual layer
    # applies to records created under the current contract; historical v2/v1
    # receipts remain usable when all their original hash checks still pass.
    if evidence.get("schema_version") == 2:
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
    expected_layers = contract.get("expected_qa_layers") if isinstance(contract, dict) else None
    qa_results = contract.get("qa_results") if isinstance(contract, dict) else None
    if (
        not isinstance(contract, dict)
        or contract.get("schema_version") != 2
        or contract.get("contract_version") != 2
        or evidence.get("visual_review_sha256") != contract.get("visual_review_sha256")
        or evidence.get("visual_review") != contract.get("visual_review")
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
        or any(result not in {"PASS", "FAIL", "NOT_APPLICABLE"} for result in qa_results.values())
        or not isinstance(contract.get("visual_review"), dict)
        or contract.get("visual_review_sha256") != hashlib.sha256(
            json.dumps(contract.get("visual_review"), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
    ):
        return None
    visual_review = contract["visual_review"]
    plan_snapshot_data: dict[str, object] = {}
    try:
        plan_snapshot_data = json.loads(plan_snapshot.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    plan_call = plan_snapshot_data.get("execution_call") if isinstance(plan_snapshot_data, dict) else None
    plan_prompt = plan_call.get("prompt") if isinstance(plan_call, dict) else None
    plan_prompt_text = plan_prompt.get("text") if isinstance(plan_prompt, dict) else None
    if (
        not isinstance(visual_review.get("author"), str)
        or not visual_review.get("author", "").strip()
        or (plan_snapshot_data.get("request_id") and visual_review.get("request_id") != plan_snapshot_data.get("request_id"))
        or not isinstance(visual_review.get("request_id"), str)
        or not isinstance(visual_review.get("attempt_id"), str)
        or not visual_review.get("attempt_id")
        or not isinstance(visual_review.get("task_revision"), int)
        or visual_review.get("task_revision", -1) < 0
        or str(visual_review.get("executed_plan_sha256", "")).casefold() != sha256(plan_snapshot).casefold()
        or not plan_prompt_text
        or str(visual_review.get("prompt_sha256", "")).casefold() != hashlib.sha256(str(plan_prompt_text).encode("utf-8")).hexdigest()
        or not re.fullmatch(r"[0-9a-f]{64}", str(visual_review.get("output_sha256", "")), flags=re.IGNORECASE)
        or not re.fullmatch(r"[0-9a-f]{64}", str(visual_review.get("prompt_sha256", "")), flags=re.IGNORECASE)
        or not re.fullmatch(r"[0-9a-f]{64}", str(visual_review.get("executed_plan_sha256", "")), flags=re.IGNORECASE)
    ):
        return None
    if not {"ANATOMY_REVIEW", "VISIBLE_DEFECTS", "PROMPT_ADHERENCE"}.issubset(expected_layers):
        return None
    expected_visual_results = {
        "ANATOMY_REVIEW": visual_review.get("anatomy_review", {}).get("status"),
        "VISIBLE_DEFECTS": visual_review.get("visible_defect_review", {}).get("status"),
        "PROMPT_ADHERENCE": visual_review.get("prompt_adherence", {}).get("status"),
    }
    if any(qa_results.get(layer) != result for layer, result in expected_visual_results.items()):
        return None
    expected_failed = sorted(name for name, result in qa_results.items() if result == "FAIL")
    if evidence.get("qa_failed") != expected_failed:
        return None
    return evidence


def generation_has_passed_qa(row: dict[str, str]) -> bool:
    """Only a manager-issued QA receipt for the registered image can establish passed QA."""
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
    target_identity: dict[str, str] | None = None,
) -> list[dict[str, object]]:
    decision = decision.upper()
    if decision == "NOT_SELECTED":
        if values:
            raise StylePackError("Auxiliary references are not selected; use --aux-body-decision SELECTED before supplying --aux-body values.")
        return []
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
        record = {
            "ref_id": ref_id,
            "mode": mode,
            "path": str(file),
            "sha256": sha256(file),
            "manifest_sha256": sha256(library / "BODY_REFERENCE_MANIFEST.csv"),
            "active_roles": sorted(required_roles),
            "body_build": row.get("body_build", ""),
            "primary_family": row.get("primary_family", ""),
            "pose": row.get("pose", ""),
            "view": row.get("view", ""),
            "camera_angle": row.get("camera_angle", ""),
            "framing": row.get("framing", ""),
            "not_for_roles": [item for item in row.get("not_for_roles", "").upper().split(";") if item],
            "source_character_id": row.get("source_character_id", "UNKNOWN") or "UNKNOWN",
            "subject_anatomy_compatibility": row.get("subject_anatomy_compatibility", "") or "UNKNOWN",
            "anatomy_compatibility": row.get("anatomy_compatibility", "UNKNOWN") or "UNKNOWN",
            "subject_anatomy_evidence_source": row.get("subject_anatomy_evidence_source", "") or "UNKNOWN",
            "anatomy_evidence_source": row.get("anatomy_evidence_source", "UNKNOWN") or "UNKNOWN",
            "style_influence": "FORBIDDEN",
            "forbidden_transfer": [
                "face", "identity", "hair", "skin_tone", "costume_design", "palette",
                "linework", "rendering_style", "lighting", "background_style", "source_medium",
            ],
        }
        if target_identity is not None:
            result = validate_reference_compatibility(target_identity, record, sorted(required_roles))
            if not result["compatible"]:
                raise StylePackError(f"{ref_id} is {result['status']} for {mode}: {result['reason']}")
            record["compatibility"] = result
        selected.append(record)
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
        "accessory": getattr(args, "accessory_reference", []),
        "face variant": getattr(args, "face_variant_reference", []),
        "body variant": getattr(args, "body_variant_reference", []),
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


def build_scene_prompt_source_contract(
    purpose: str,
    character_id: str,
    selected_local_roles: dict[str, str],
) -> dict[str, dict[str, str]]:
    """Declare optional scene roles sourced from the exact executable prompt."""
    if purpose.upper() != "SCENE" or not re.fullmatch(r"CHAR_\d+", character_id.upper()):
        return {}
    return {
        role: {
            "source": "EXACT_EXECUTABLE_PROMPT",
            "evidence_required": "USER_SPECIFIED_SCENE_TEXT",
        }
        for role in ("POSE", "CLOTHES", "ACCESSORY", "LIGHTING", "BACKGROUND", "COMPOSITION")
        if not str(selected_local_roles.get(role, "")).strip()
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
    if not isinstance(style_records, list):
        raise StylePackError("Multi-stage STYLE references must be a list.")
    style_anchor = style_records[0] if style_records else None
    stages: list[dict[str, object]] = []

    def source_records(keys: Sequence[str], include_style: bool = True) -> list[dict[str, object]]:
        records: list[dict[str, object]] = []
        if include_style and style_stage_record is not None:
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
            if record.get("generated_stage_output"):
                # Keep the stage's semantic role for exact generated-source and
                # targeted-pack provenance checks at READY/START.
                item["stage_role"] = str(record.get("stage_role", ""))
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

    face_keys = [key for key in ("primary_face", "supporting_face", "expression", "face_variant") if key in selected]
    has_face_variant = bool(selected.get("face_variant"))
    has_body_variant = bool(selected.get("body_variant"))
    has_accessory = bool(selected.get("accessory"))
    face_variant_condition = (
        " Preserve every selected FACE_VARIANT as a distinct approved detail; do not reinterpret it as canonical identity or merge it into another role."
        if has_face_variant else ""
    )
    body_variant_condition = (
        " Preserve the selected BODY_VARIANT details through this stage without silently replacing the canonical character base."
        if has_body_variant else ""
    )
    accessory_condition = (
        " Preserve the selected approved ACCESSORY design, scale, placement, and visible details through this stage."
        if has_accessory else ""
    )
    if face_keys and purpose != "CHARACTER_BASE":
        add_stage(
            "01_FACE_IDENTITY",
            "Resolve face geometry and expression in the source drawing style; do not invent body proportions." + face_variant_condition,
            source_records(face_keys),
            ("FACE_GEOMETRY", "EXPRESSION", "STYLE"),
        )

    body_records = source_records([key for key in ("character_assembly", "body", "pose", "body_variant") if key in selected])
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
            *([{**style_stage_record, "stage_role": "STYLE"}] if style_stage_record is not None else []),
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
        "Resolve the dominant silhouette, proportions, pose, camera, contacts, and foreshortening on a neutral outfit/background." + body_variant_condition,
        body_records,
        ("BODY_SILHOUETTE", "BODY_PROPORTIONS", "POSE_CONTACTS", "CAMERA", "STYLE"),
    )

    body_output = placeholder("02_BODY_POSE", "BODY_POSE_STAGE")
    character_base_output = body_output
    if "clothes" in selected or "accessory" in selected:
        add_stage(
            "03_CLOTHING",
            "Apply selected clothing and accessories to the verified body/pose without changing its silhouette, anatomy, or camera." + body_variant_condition + accessory_condition,
            [*([{**style_stage_record, "stage_role": "STYLE"}] if style_stage_record is not None else []), body_output, *source_records([key for key in ("clothes", "accessory") if key in selected], include_style=False)],
            ("CLOTHING", "BODY_SILHOUETTE", "BODY_PROPORTIONS", "STYLE"),
        )
        character_base_output = placeholder("03_CLOTHING", "CLOTHING_STAGE")

    composite_inputs = [*([{**style_stage_record, "stage_role": "STYLE"}] if style_stage_record is not None else []), character_base_output]
    if face_keys:
        composite_inputs.append(placeholder("01_FACE_IDENTITY", "FACE_IDENTITY_STAGE"))
    add_stage(
        "04_CHARACTER_COMPOSITE",
        "Combine the verified face with the verified clothed body; preserve both layers exactly." + face_variant_condition + body_variant_condition + accessory_condition,
        composite_inputs,
        ("FACE_GEOMETRY", "BODY_SILHOUETTE", "BODY_PROPORTIONS", "CLOTHING", "STYLE"),
    )

    final_inputs = [
        *([{**style_stage_record, "stage_role": "STYLE"}] if style_stage_record is not None else []),
        placeholder("04_CHARACTER_COMPOSITE", "CHARACTER_COMPOSITE_STAGE"),
        *source_records(
            [key for key in ("lighting", "background", "composition") if key in selected],
            include_style=False,
        ),
    ]
    add_stage(
        "05_FINAL_SCENE",
        "Place the verified character into the final scene without reopening face or body design." + face_variant_condition + body_variant_condition + accessory_condition,
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
        except StylePackError as error:
            if mode == "SINGLE_PASS":
                raise
            if purpose == "SCENE":
                raise StylePackError(
                    "A SCENE is one requested deliverable and cannot auto-expand into generated helper images. "
                    "Reduce the attached references or explicitly request MULTI_STAGE with user authorization."
                ) from error
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
    semantic_snapshot = reusable_preparation_semantics(args)
    if semantic_snapshot is not None:
        startup_interaction = semantic_snapshot["startup_interaction"]
        reviewed_counts = semantic_snapshot["reviewed_counts"]
    else:
        startup_interaction = parse_startup_interaction(args)
        reviewed_counts = parse_reviewed_counts(args.reviewed, args.face_candidates_reviewed)
        validate_prompt_only_body_library_review(args)
    purpose = args.generation_purpose.upper()
    overrides = {item.upper() for item in args.override}
    character_id = args.character_id.upper()
    is_new_character = character_id == "NEW"
    confirmed_profile: dict[str, object] | None = None
    profile_default_application: dict[str, list[dict[str, str]]] | None = None
    profile_default_exceptions: dict[str, list[str]] | None = None
    if purpose == "SCENE" and re.fullmatch(r"CHAR_\d+", character_id):
        confirmed_profile = effective_character_profile(paths, character_id)
        profile_default_exceptions = {
            "explicit": [role for role, value in (("wardrobe", args.clothes_reference), ("accessory", args.accessory_reference),
                        ("face_variant", args.face_variant_reference), ("body_variant", args.body_variant_reference)) if value],
            "suppressed": [str(role).upper() for role in getattr(args, "suppress_profile_default", [])],
            "overrides": sorted(overrides),
        }
        profile_default_application = apply_confirmed_defaults(paths, args, character_id, confirmed_profile, overrides)
    scene_prompt_sources = build_scene_prompt_source_contract(purpose, character_id, {
        "POSE": args.pose_reference,
        "CLOTHES": args.clothes_reference,
        "ACCESSORY": args.accessory_reference,
        "LIGHTING": args.lighting_reference,
        "BACKGROUND": args.background_reference,
        "COMPOSITION": args.composition_reference,
    })
    overrides.update(scene_prompt_sources)
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
        if args.framing == "FULL_BODY" and not args.body_silhouette_notes.strip():
            missing_body_fields.append("--body-silhouette-notes")
        if purpose == "SCENE" and re.fullmatch(r"CHAR_\d+", character_id) and not args.character_reference_evidence.strip():
            missing_body_fields.append("--character-reference-evidence")
        if missing_body_fields:
            raise StylePackError(
                "Character-bearing generation requires: " + ", ".join(missing_body_fields)
            )
    context = _minimal_preparation_style_context(paths)
    target_identity = compatibility_target(paths, args, character_id)
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
        if args.clothes_reference or args.accessory_reference or args.face_variant_reference or args.body_variant_reference or args.lighting_reference or args.background_reference or args.composition_reference:
            raise StylePackError(
                "CHARACTER_BASE does not accept clothes, accessories, face/body variants, lighting, background, or composition references. "
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
        target_identity,
    )

    resolved_user_selections = startup_interaction.get("resolved_user_selections", {})
    selected_style_choice = str(resolved_user_selections.get("style", "")).upper()
    allow_character_default_style = (
        selected_style_choice == "GENERATOR_DEFAULT"
        and bool(re.fullmatch(r"CHAR_\d+", character_id))
        and purpose == "SCENE"
    )
    if not args.style_reference and not allow_character_default_style:
        raise StylePackError("At least one local --style-reference is required unless the user explicitly selected GENERATOR_DEFAULT for an approved character scene.")
    if args.style_reference and selected_style_choice == "GENERATOR_DEFAULT":
        raise StylePackError("GENERATOR_DEFAULT cannot attach project STYLE reference slots.")
    selected: dict[str, object] = {}
    if args.style_reference:
        selected["style"] = [
            validate_plan_reference(
                paths,
                value,
                "STYLE",
                allow_request_local_style_candidate=(
                    startup_interaction.get("profile_confirmation_complete") is True
                    and startup_interaction.get("style_confirmation_complete") is True
                    and bool(str(startup_interaction.get("confirmed_style_name", "")).strip())
                    and style_slug(str(startup_interaction.get("confirmed_style_name", ""))) == style_slug(paths.style_name)
                ),
            )
            for value in args.style_reference
        ]
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
        require_approved_character_asset_role(paths, Path(str(assembly["path"])), character_id, "BASE", "CHARACTER_ASSEMBLY")
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
            require_approved_character_asset_role(paths, Path(str(body["path"])), character_id, "BODY", "NEAREST_CHARACTER_BODY")
            if not is_relative_to(Path(str(body["path"])), identity_folder):
                raise StylePackError("The nearest body view must belong to the selected approved character folder.")
            selected["primary_face"] = assembly
            selected["body"] = body
        else:
            if selected_body_view == "ASSEMBLY" or not args.body_reference or not args.primary_face:
                raise StylePackError("IDENTITY_STRICT requires separate approved face and nearest front/side/back body references.")
            primary_face = validate_plan_reference(paths, args.primary_face, "CHARACTER_FACE")
            body = validate_plan_reference(paths, args.body_reference, "NEAREST_CHARACTER_BODY")
            require_approved_character_asset_role(paths, Path(str(primary_face["path"])), character_id, "FACE", "CHARACTER_FACE")
            require_approved_character_asset_role(paths, Path(str(body["path"])), character_id, "BODY", "NEAREST_CHARACTER_BODY")
            if not is_relative_to(Path(str(primary_face["path"])), identity_folder) or not is_relative_to(Path(str(body["path"])), identity_folder):
                raise StylePackError("Strict face and body references must belong to the selected approved character folder.")
            selected["primary_face"] = primary_face
            selected["body"] = body
        if args.supporting_face:
            selected["supporting_face"] = validate_plan_reference(paths, args.supporting_face, "SUPPORTING_FACE_STYLE")
        if args.expression_reference:
            selected["expression"] = validate_plan_reference(paths, args.expression_reference, "FACE_EXPRESSION")
        face_library_count = 0
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

        face_library_count = 0
    else:
        face_library_count = 0

    category_values = {
        "BODY": args.body_reference,
        "POSE": args.pose_reference,
        "CLOTHES": args.clothes_reference,
        "LIGHTING": args.lighting_reference,
        "BACKGROUND": args.background_reference,
        "COMPOSITION": args.composition_reference,
    }
    for category, value in category_values.items():
        if category == "POSE" and not value and "pose" not in selected:
            continue  # The requested pose may be specified entirely by text.
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

    if args.clothes_reference and is_relative_to(Path(str(selected["clothes"]["path"])), paths.generations):
        wardrobe = registered_approved_character_asset(paths, Path(str(selected["clothes"]["path"])))
        if not wardrobe or wardrobe.get("asset_role") != "WARDROBE" or wardrobe.get("character_id") != character_id:
            raise StylePackError("Generation-library CLOTHES reference must be an exact APPROVED_WARDROBE asset for the selected character.")
    if args.accessory_reference:
        selected["accessory"] = []
        for value in args.accessory_reference:
            accessory = validate_plan_reference(paths, value, "ACCESSORY")
            registered = registered_approved_character_asset(paths, Path(str(accessory["path"])))
            if not registered or registered.get("asset_role") != "ACCESSORY" or registered.get("character_id") != character_id:
                raise StylePackError("ACCESSORY references must be exact APPROVED_ACCESSORY assets for the selected character.")
            selected["accessory"].append(accessory)
    for option, key, expected_role, category in (
        (args.face_variant_reference, "face_variant", "FACE_VARIANT", "FACE"),
        (args.body_variant_reference, "body_variant", "BODY_VARIANT", "BODY"),
    ):
        if option:
            selected[key] = []
            for value in option:
                reference = validate_plan_reference(paths, value, category)
                registered = registered_approved_character_asset(paths, Path(str(reference["path"])))
                if not registered or registered.get("asset_role") != expected_role or registered.get("character_id") != character_id:
                    raise StylePackError(f"{expected_role} references must be exact approved role assets for the selected character and remain distinct from canonical identity.")
                selected[key].append(reference)

    active_roles_by_key = active_roles_by_selection_key(existing_scene, character_free_scene)
    compatibility_records: list[dict[str, object]] = []
    for key, value in selected.items():
        records = value if isinstance(value, list) else [value]
        roles = active_roles_by_key.get(key, [])
        if not roles:
            continue
        for record in records:
            decision = enforce_reference_compatibility(target_identity, record, roles)
            compatibility_records.append({"path": record["path"], "roles": roles, **decision})
    for record in auxiliary_body_references:
        compatibility_records.append({"path": record["path"], "roles": record["active_roles"], **record.get("compatibility", {})})

    selected_source_reviews = parse_selected_source_reviews(
        args.reviewed_source, selected, active_roles_by_key, auxiliary_body_references,
    )
    review_pool_counts: dict[str, int] = {}

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
        "schema_version": 6,
        "semantic_qa_schema": 4,
        "gate_status": "PREPARED_AWAITING_EXECUTABLE_CALL",
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
            "resolved_after_menu_choice": startup_interaction.get("selection_state") != "DIRECT_PARAMETERS_CONFIRMED",
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
        "confirmed_character_profile": confirmed_profile,
        "profile_default_application": profile_default_application,
        "profile_default_exceptions": profile_default_exceptions,
        "target_identity_compatibility": {
            "target": target_identity,
            "references": compatibility_records,
        },
        "generation_purpose": purpose,
        "scene_contract": scene_contract,
        "scene_semantic_requirements": (
            ["SUBJECT_ACCURACY", "CLOTHING", "POSE_CONTACTS", "OBJECTS_AND_ACTION", "BACKGROUND", "COMPOSITION"]
            if purpose == "SCENE" and scene_contract.get("applicable") and scene_contract.get("has_character")
            else []
        ),
        "scene_prompt_sources": scene_prompt_sources,
        "local_context": {
            "inventory_status": "NOT_SCANNED_DURING_PREPARATION",
            "local_files_total": None,
            "work_collection_counts": {},
            "role_counts": {},
            "review_pool_counts": {},
            "reported_pool_review_counts_unverified": reviewed_counts,
            "explicit_anchor_files": context["explicit_anchor_files"],
            "anchor_fallback_required": not bool(context["explicit_anchor_files"]),
        },
        "face_review": {
            "face_visible": face_visible,
            "face_library_count": face_library_count,
            "reported_candidate_review_count_unverified": reviewed_counts.get("FACE", 0),
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
        "risk_assessment": {"status": "AWAITING_EXACT_EXECUTABLE_CALL"},
        "execution_call": None,
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
        "selected_source_reviews": selected_source_reviews,
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
    print(f"REFERENCE_PLAN={plan_path}")
    print(f"LOCAL_FILES_RECOGNIZED={context.get('local_files_total') or 'NOT_SCANNED'}")
    print(f"REVIEW_POOL_COUNTS={json.dumps(review_pool_counts, ensure_ascii=False)}")
    print(f"REPORTED_POOL_REVIEW_COUNTS_UNVERIFIED={json.dumps(reviewed_counts, ensure_ascii=False)}")
    print(f"ANCHOR_FALLBACK_REQUIRED={str(not bool(context['explicit_anchor_files'])).upper()}")
    print(f"AUX_BODY_DECISION={args.aux_body_decision.upper()}")
    print(f"AUX_BODY_REFERENCES={len(auxiliary_body_references)}")
    print(f"ASPECT_RATIO={canvas_contract['aspect_ratio']}")
    print(f"ORIENTATION={canvas_contract['orientation']}")
    print(f"DOMINANT_BODY_SOURCE={body_proportion_contract['dominant_source']}")
    print(f"REFERENCE_WORKFLOW={generation_workflow['mode']}")
    print("GENERATION_RISK=AWAITING_EXACT_EXECUTABLE_CALL")
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
    print("STATUS=PREPARED_AWAITING_EXECUTABLE_CALL")


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
    assert_active_request_path(plan_path)
    if not plan_path.is_file():
        raise StylePackError(f"Reference plan does not exist: {plan_path}")
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as error:
        raise StylePackError(f"Cannot read reference plan: {error}") from error
    assert_profile_plan_current(paths, plan)
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
        valid_startup_selections = {*STARTUP_CHOICES, "DIRECT_CONFIRMED"}
        if not isinstance(startup, dict) or startup.get("selected") not in valid_startup_selections:
            raise StylePackError("Reference plan has no valid directly confirmed or optional-menu startup parameters.")
        if startup.get("selection_state") in {"NEW_SELECTION", "USER_CONFIRMED_FROM_TEXT_MENU", "USER_CONFIRMED_AFTER_NATIVE_UNAVAILABLE", "DIRECT_PARAMETERS_CONFIRMED", "REUSED_WITH_USER_RESELECTION"} and not str(startup.get("user_choice_quote", "")).strip():
            raise StylePackError("Reference plan has no recorded user startup choice or confirmation.")
        valid_states = {
            "NEW_SELECTION", "USER_CONFIRMED_FROM_TEXT_MENU", "DIRECT_PARAMETERS_CONFIRMED",
            "REUSED_IN_SAME_CHAT", "REUSED_WITH_USER_RESELECTION",
        }
        if startup.get("selection_state") not in valid_states:
            raise StylePackError("Reference plan has no valid new-or-reused same-chat startup state.")
        if startup.get("selection_state") in {"DIRECT_PARAMETERS_CONFIRMED", "REUSED_IN_SAME_CHAT", "REUSED_WITH_USER_RESELECTION"} and startup.get("menu_presented_this_turn") is not False:
            raise StylePackError("A directly confirmed or reused profile must not present a duplicate startup menu.")
        if startup.get("selection_state") in {"DIRECT_PARAMETERS_CONFIRMED", "REUSED_IN_SAME_CHAT", "REUSED_WITH_USER_RESELECTION", "NEW_SELECTION", "USER_CONFIRMED_FROM_TEXT_MENU"}:
            provenance = startup.get("confirmed_provenance")
            if not isinstance(provenance, dict) or not all(str(provenance.get(key, "")).strip() for key in ("chat_id", "message_id", "quote")):
                raise StylePackError("Startup profiles require chat, message, and quote provenance.")
            origin_state = str(startup.get("selection_origin_state", startup.get("selection_state", "")))
            if origin_state in {"DIRECT_PARAMETERS_CONFIRMED", "REUSED_WITH_USER_RESELECTION"} and not re.search(rf"(?<!\d){fidelity}\s*%?(?!\d)", str(provenance["quote"])):
                raise StylePackError("Direct confirmation provenance does not support the prepared fidelity.")
        resolved_profile = startup.get("resolved_parameters")
        if schema_version >= 6:
            validate_startup_profile_evidence(startup, fidelity)
        if startup.get("selection_state") == "USER_CONFIRMED_FROM_TEXT_MENU" and (
            startup.get("menu_presented_this_turn") is not True
            or startup.get("menu_surface_this_turn") != "TEXT_NUMBERED_MENU"
        ):
            raise StylePackError("A text-menu profile must record the visible TEXT_NUMBERED_MENU.")
        if startup.get("selection_state") == "NEW_SELECTION" and (
            startup.get("menu_presented_this_turn") is not True
            or startup.get("menu_surface_this_turn") not in {"NATIVE_CONTEXT_MENU", "TEXT_NUMBERED_MENU"}
        ):
            raise StylePackError("A new startup profile must record the visible menu surface used this turn.")
        options = startup.get("options")
        if startup.get("menu_contract") == "THREE_AI_PRESETS_PLUS_CUSTOM" and (
            not isinstance(options, list) or [item.get("id") for item in options] != [*STARTUP_CHOICES]
        ):
            raise StylePackError("Reference plan does not contain the required three AI presets plus CUSTOM.")
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
    required.extend(("ANATOMY_REVIEW", "VISIBLE_DEFECTS", "PROMPT_ADHERENCE"))
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
    if plan.get("generation_purpose") == "SCENE" and isinstance(scene_contract, dict) and scene_contract.get("applicable") and scene_contract.get("has_character"):
        required.extend(plan.get("scene_semantic_requirements", [
            "SUBJECT_ACCURACY", "CLOTHING", "POSE_CONTACTS", "OBJECTS_AND_ACTION", "BACKGROUND", "COMPOSITION"
        ]))

    qa_values = {
        "ATTACHMENTS": args.qa_attachments,
        "CANVAS": args.qa_canvas,
        "STAGE_LAYER": args.qa_stage_layer,
        "FACE_GEOMETRY": args.qa_face,
        "BODY_SILHOUETTE": args.qa_body_silhouette,
        "BODY_PROPORTIONS": args.qa_body_proportions,
        "LIMB_PROPORTIONS": getattr(args, "qa_limb_proportions", "NOT_CHECKED"),
        "ANATOMY_REVIEW": getattr(args, "visual_anatomy_status", "NOT_CHECKED"),
        "VISIBLE_DEFECTS": getattr(args, "visual_defects_status", "NOT_CHECKED"),
        "PROMPT_ADHERENCE": getattr(args, "visual_prompt_status", "NOT_CHECKED"),
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
            "OBJECTS_AND_ACTION": getattr(args, "qa_objects_action", "NOT_CHECKED"),
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
    for style in discover_style_packs(paths.workspace, paths.pack):
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
        if any(part.upper() in {"00_PENDING", "REJECTED", "DRAFT", "STAGING", "TEST"} for part in alternative.parts):
            assert_active_request_path(alternative)
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
            "anatomy_compatibility": args.anatomy_compatibility.upper(),
            "anatomy_evidence_source": args.anatomy_evidence_source,
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
    visual_review: dict[str, object],
) -> Path:
    """Emit immutable plan/contract snapshots only after evaluated required QA at any fidelity."""
    evidence_path = style_file.with_suffix(style_file.suffix + ".qa-evidence.json")
    plan_snapshot = style_file.with_suffix(style_file.suffix + ".qa-plan.json")
    contract_path = style_file.with_suffix(style_file.suffix + ".qa-contract.json")
    shutil.copy2(reference_plan, plan_snapshot)
    contract = {
        "schema_version": 2,
        "contract_version": 2,
        "plan_snapshot": str(plan_snapshot),
        "plan_content_sha256": sha256(plan_snapshot),
        "stage_id": stage_id,
        "expected_qa_layers": sorted(qa_required),
        "qa_results": {name: qa_results[name] for name in sorted(qa_required)},
        "visual_review": visual_review,
        "visual_review_sha256": hashlib.sha256(
            json.dumps(visual_review, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
    }
    contract_path.write_text(json.dumps(contract, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    receipt = {
        "schema_version": 3,
        "record_status": status,
        "output_sha256": sha256(style_file),
        "qa_contract": str(contract_path),
        "qa_contract_sha256": sha256(contract_path),
        "visual_review_sha256": contract["visual_review_sha256"],
        "visual_review": visual_review,
        "qa_failed": sorted(name for name, result in qa_results.items() if result == "FAIL"),
    }
    evidence_path.write_text(json.dumps(receipt, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    return evidence_path


def prompt_review_clauses(prompt_text: str) -> list[str]:
    """Split exact prompt text into checklist clauses for visual adherence review."""
    return [
        part.strip(" \t\r\n,;:-")
        for part in re.split(
            r"(?:[.!?;\n]+|,|\b(?:and|while|but|then|и|а|но|затем|при этом|а также)\b)",
            prompt_text,
            flags=re.IGNORECASE,
        )
        if part.strip(" \t\r\n,;:-")
    ]


def validate_visual_review(
    review_path: Path,
    *,
    request_id: str,
    attempt_id: str,
    task_revision: int,
    output: Path,
    execution_snapshot: dict[str, object],
    plan: dict[str, object],
    stage_id: str,
) -> dict[str, object]:
    """Validate human visual findings against the immutable executed attempt."""
    try:
        report = json.loads(review_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise StylePackError(f"Cannot read --visual-review-json: {error}") from error
    if not isinstance(report, dict) or report.get("schema_version") != 1:
        raise StylePackError("--visual-review-json must be an object with schema_version=1.")
    executed_call = execution_snapshot.get("execution_call")
    prompt = executed_call.get("prompt") if isinstance(executed_call, dict) else None
    prompt_text = prompt.get("text") if isinstance(prompt, dict) else None
    prompt_hash = hashlib.sha256(str(prompt_text or "").encode("utf-8")).hexdigest()
    plan_hash = str(execution_snapshot.get("reference_plan_snapshot_sha256", "")).lower()
    expected = {
        "request_id": request_id,
        "attempt_id": attempt_id,
        "task_revision": task_revision,
        "output_sha256": sha256(output),
        "prompt_sha256": prompt_hash,
        "executed_plan_sha256": plan_hash,
    }
    for key, value in expected.items():
        actual = report.get(key)
        if isinstance(value, str):
            actual = str(actual or "").lower()
            value = value.lower()
        if actual != value:
            raise StylePackError(f"--visual-review-json {key} does not match the verified active attempt/output.")
    if not prompt_text or prompt_hash != str(execution_snapshot.get("prompt_sha256", "")).lower():
        raise StylePackError("The immutable attempt snapshot does not contain a verifiable exact executed prompt.")
    author = report.get("author")
    if not isinstance(author, str) or not author.strip():
        raise StylePackError("--visual-review-json requires an explicit non-empty author.")

    anatomy = report.get("anatomy_review")
    defects = report.get("visible_defect_review")
    adherence = report.get("prompt_adherence")
    if not isinstance(anatomy, dict) or anatomy.get("status") not in {"PASS", "FAIL", "NOT_APPLICABLE"}:
        raise StylePackError("Visual review requires anatomy_review.status PASS, FAIL, or NOT_APPLICABLE.")
    anatomy_reason = anatomy.get("reason")
    if anatomy["status"] == "NOT_APPLICABLE":
        if anatomy.get("no_visible_anatomy") is not True:
            raise StylePackError("Anatomy NOT_APPLICABLE requires no_visible_anatomy=true.")
        if not isinstance(anatomy_reason, str) or len(anatomy_reason.strip()) < 20:
            raise StylePackError("Anatomy NOT_APPLICABLE requires a substantive reason of at least 20 characters.")
        canvas_contract = plan.get("canvas_contract")
        full_body = bool(canvas_contract.get("full_figure")) if isinstance(canvas_contract, dict) else False
        face_review = plan.get("face_review")
        scene_contract = plan.get("scene_contract")
        visible_identity = bool(face_review.get("face_visible")) if isinstance(face_review, dict) else False
        visible_identity = visible_identity or bool(scene_contract.get("has_character")) if isinstance(scene_contract, dict) else visible_identity
        visible_identity = visible_identity or str(plan.get("generation_purpose", "")).upper() == "CHARACTER_BASE"
        full_body = full_body or stage_id in {
            "02_PHYSIQUE_FRONT", "03_PHYSIQUE_SIDE", "04_PHYSIQUE_BACK", "05_CHARACTER_ASSEMBLY"
        }
        if full_body:
            raise StylePackError("Anatomy review cannot be NOT_APPLICABLE for a full-body or physique/assembly stage.")
        if visible_identity:
            raise StylePackError("Anatomy review cannot be NOT_APPLICABLE for a plan with a visible character or face.")
    else:
        anatomy_scope = anatomy.get("checked_scope")
        anatomy_findings = anatomy.get("findings")
        if not isinstance(anatomy_scope, str) or len(anatomy_scope.strip()) < 10 or not isinstance(anatomy_findings, list):
            raise StylePackError("Anatomy PASS/FAIL requires a substantive checked_scope and findings list.")
        if anatomy["status"] == "PASS" and anatomy_findings:
            raise StylePackError("Anatomy PASS cannot contain unresolved findings.")
        if anatomy["status"] == "FAIL" and not anatomy_findings:
            raise StylePackError("Anatomy FAIL requires at least one finding.")
    if not isinstance(defects, dict) or defects.get("status") not in {"PASS", "FAIL"}:
        raise StylePackError("Visual review requires visible_defect_review.status PASS or FAIL.")
    checked_scope = defects.get("checked_scope")
    findings = defects.get("findings")
    if not isinstance(checked_scope, str) or len(checked_scope.strip()) < 10 or not isinstance(findings, list):
        raise StylePackError("Visible-defect review requires a substantive checked_scope and a findings list.")
    if defects["status"] == "FAIL" and not findings:
        raise StylePackError("Visible-defect review FAIL requires at least one finding.")
    if defects["status"] == "PASS" and findings:
        raise StylePackError("Visible-defect review PASS cannot contain unresolved findings.")
    if not isinstance(adherence, dict) or adherence.get("status") not in {"PASS", "FAIL"}:
        raise StylePackError("Visual review requires prompt_adherence.status PASS or FAIL.")
    constraints = adherence.get("constraints")
    # Require a reviewer-authored checklist for every clause-like segment in
    # the exact prompt. This is a structural completeness check, not a claim
    # that syntax can determine semantic atomicity in free-form language.
    prompt_requirements = prompt_review_clauses(str(prompt_text))
    if (
        adherence.get("checked_scope") != "FULL_EXECUTED_PROMPT"
        or adherence.get("all_explicit_constraints_assessed") is not True
        or not isinstance(constraints, list)
        or len(constraints) != len(prompt_requirements)
    ):
        raise StylePackError("Prompt-adherence review must assess every explicit constraint in the full executed prompt.")
    for index, constraint in enumerate(constraints, start=1):
        if (
            not isinstance(constraint, dict)
            or not isinstance(constraint.get("constraint"), str)
            or not constraint["constraint"].strip()
            or constraint.get("status") not in {"PASS", "FAIL"}
            or not isinstance(constraint.get("evidence"), str)
            or not constraint["evidence"].strip()
        ):
            raise StylePackError(f"Prompt-adherence constraint #{index} requires text, PASS/FAIL, and evidence.")
        if constraint["constraint"].strip(" \t\r\n,;:-") != prompt_requirements[index - 1]:
            raise StylePackError(
                "Prompt-adherence constraints must match every clause of the exact executed prompt in order; "
                f"constraint #{index} does not match its prompt clause."
            )
    failed_constraints = [row for row in constraints if row["status"] == "FAIL"]
    if adherence["status"] == "PASS" and failed_constraints:
        raise StylePackError("Prompt-adherence PASS conflicts with a failed explicit constraint.")
    if adherence["status"] == "FAIL" and not failed_constraints:
        raise StylePackError("Prompt-adherence FAIL requires at least one failed explicit constraint.")
    return report


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
    # Validate the exact request/attempt/plan binding before writing any archive or manifest row.
    archive_file = existing_archive_for_image(paths, image)
    output_reference = archive_file or image
    request_id = safe_component(args.request_id, "request")
    execution_guard_path = paths.generations / "00_PENDING" / request_id / "EXECUTION_GUARD.json"
    try:
        guard_state = load_execution_guard(execution_guard_path)
    except ExecutionGuardError as error:
        raise StylePackError(
            f"Generated image at {output_reference} cannot be registered because the execution guard blocks it: {error}"
        ) from error
    if guard_state.get("request_id") != request_id:
        raise StylePackError(f"Generated image at {output_reference} cannot be registered because the execution guard belongs to another request.")
    matching_attempt = next(
        (row for row in guard_state.get("attempts", []) if isinstance(row, dict) and row.get("attempt_id") == args.attempt_id),
        None,
    )
    attempt_status = matching_attempt.get("status") if isinstance(matching_attempt, dict) else None
    if not isinstance(matching_attempt, dict) or attempt_status not in {"ACTIVE", "UNKNOWN", "RESULT_AVAILABLE"}:
        raise StylePackError(
            f"Generated image at {output_reference} cannot be registered because --attempt-id does not identify an active, UNKNOWN, or already-visible request attempt."
        )
    if attempt_status == "RESULT_AVAILABLE":
        evidence = matching_attempt.get("result_evidence", [])
        available_result = next(
            (
                row for row in guard_state.get("available_results", [])
                if isinstance(row, dict) and row.get("attempt_id") == args.attempt_id
            ),
            None,
        )
        latest_attempt_id = next(
            (row.get("attempt_id") for row in reversed(guard_state.get("attempts", [])) if isinstance(row, dict)),
            None,
        )
        def matches_evidence(recorded: object) -> bool:
            if not isinstance(recorded, list):
                return False
            for item in recorded:
                value = str(item).strip()
                if not value:
                    continue
                evidence_path = Path(value).resolve()
                if evidence_path == image.resolve():
                    return True
                if evidence_path.is_file() and sha256(evidence_path) == sha256(image):
                    return True
            return False

        def matches_reconciled_evidence() -> bool:
            if not isinstance(available_result, dict):
                return False
            recorded = available_result.get("evidence")
            digests = available_result.get("evidence_sha256")
            if not isinstance(recorded, list) or not isinstance(digests, list) or len(recorded) != len(digests):
                return False
            for item, digest in zip(recorded, digests):
                evidence_path = Path(str(item)).resolve()
                if evidence_path == image.resolve() and str(digest).lower() == sha256(image):
                    return True
            return False

        ordinary_available = (
            guard_state.get("phase") == "RESULT_AVAILABLE"
            and guard_state.get("status") == "ACTIVE"
            and isinstance(available_result, dict)
            and not available_result.get("late")
        )
        reconciled_available = (
            guard_state.get("phase") == "ATTEMPT_AVAILABLE"
            and guard_state.get("status") == "ACTIVE"
            and guard_state.get("next_required_action") == "NEXT_SAFE_EXECUTION_OR_COMPLETE"
            and isinstance(available_result, dict)
            and available_result.get("late") is True
            and matching_attempt.get("reconciliation", {}).get("outcome") == "AVAILABLE"
        )
        attempt_revision = int(matching_attempt.get("task_revision", -1))
        current_revision = int(guard_state.get("task_revision", 0))
        result_event_indices = [
            index
            for index, event in enumerate(guard_state.get("events", []))
            if isinstance(event, dict)
            and event.get("attempt_id") == args.attempt_id
            and event.get("event") in {"VISIBLE_RESULT", "ATTEMPT_RECONCILED"}
        ]
        result_event_index = max(result_event_indices, default=-1)
        corrected_after_result = bool(
            current_revision > attempt_revision
            and result_event_index >= 0
            and any(
                isinstance(event, dict) and event.get("event") == "USER_CORRECTION"
                for event in guard_state.get("events", [])[result_event_index + 1:]
            )
        )
        historical_correction_available = (
            guard_state.get("status") in {"ACTIVE", "READY"}
            and guard_state.get("active_attempt") is None
            and isinstance(available_result, dict)
            and matching_attempt.get("status") == "RESULT_AVAILABLE"
            and corrected_after_result
            and (
                (not available_result.get("late") and not matching_attempt.get("reconciliation"))
                or (available_result.get("late") is True and matching_attempt.get("reconciliation", {}).get("outcome") == "AVAILABLE")
            )
        )
        if not (
            latest_attempt_id == args.attempt_id
            and guard_state.get("active_attempt") is None
            and (ordinary_available or reconciled_available or historical_correction_available)
            and matches_evidence(evidence)
            and matches_evidence(available_result.get("evidence") if isinstance(available_result, dict) else None)
            and (not reconciled_available or matches_reconciled_evidence())
        ):
            raise StylePackError(
                f"Generated image at {output_reference} cannot be registered because --image does not match the exact path recorded for the already-visible attempt."
            )
        active_attempt = matching_attempt
        already_visible = True
    elif attempt_status == "ACTIVE":
        already_visible = False
        try:
            guard_state = require_execution_started(execution_guard_path, request_id)
        except ExecutionGuardError as error:
            raise StylePackError(
                f"Generated image at {output_reference} cannot be registered because the execution guard blocks it: {error}"
            ) from error
        active_attempt = guard_state.get("active_attempt")
        if not isinstance(active_attempt, dict) or active_attempt.get("attempt_id") != args.attempt_id:
            raise StylePackError(f"Generated image at {output_reference} cannot be registered because --attempt-id is not the active request attempt.")
    else:
        # Preserve guard UNKNOWN/STOP state until QA finishes; a valid late
        # output is then recorded through VISIBLE_RESULT and remains late.
        active_attempt = matching_attempt
        already_visible = False
    execution_snapshot = active_attempt.get("execution_snapshot") if isinstance(active_attempt, dict) else None
    if not isinstance(execution_snapshot, dict):
        raise StylePackError(
            f"Generated image at {output_reference} cannot be registered because the attempt has no durable executed-call snapshot."
        )
    snapshot_binding = execution_snapshot.get("reference_binding")
    if (
        execution_snapshot.get("request_id") != request_id
        or execution_snapshot.get("attempt_id") != args.attempt_id
        or execution_snapshot.get("task_revision") != active_attempt.get("task_revision")
        or execution_snapshot.get("guard_stage", execution_snapshot.get("stage")) != active_attempt.get("stage")
        or not isinstance(snapshot_binding, dict)
        or snapshot_binding != active_attempt.get("reference_binding")
        or execution_snapshot.get("prompt_sha256") != snapshot_binding.get("prompt_sha256")
        or execution_snapshot.get("execution_call_sha256") != snapshot_binding.get("execution_call_sha256")
    ):
        raise StylePackError(
            f"Generated image at {output_reference} cannot be registered because the durable executed-call snapshot is missing, stale, or inconsistent with this request attempt."
        )
    provider_artifact = execution_snapshot.get("provider_artifact")
    if isinstance(provider_artifact, dict):
        artifact_path = Path(str(provider_artifact.get("path", ""))).resolve()
        if (
            not artifact_path.is_file()
            or str(provider_artifact.get("sha256", "")).lower() != sha256(artifact_path)
            or sha256(image) != str(provider_artifact.get("sha256", "")).lower()
        ):
            raise StylePackError(
                f"Generated image at {output_reference} cannot be registered because it does not match the provider artifact bound to the executed-call snapshot."
            )
    registration_plan_arg = args.reference_plan
    if args.reference_plan and isinstance(snapshot_binding, dict):
        requested_plan_path = Path(args.reference_plan).expanduser()
        if not requested_plan_path.is_absolute():
            requested_plan_path = paths.workspace / requested_plan_path
        requested_plan_path = requested_plan_path.resolve()
        bound_plan_path = Path(str(snapshot_binding.get("path", ""))).resolve()
        snapshot_plan_path = Path(str(execution_snapshot.get("reference_plan_snapshot_path", ""))).resolve()
        expected_request_folder = (paths.generations / "00_PENDING" / request_id).resolve()
        if (
            requested_plan_path != bound_plan_path
            or snapshot_plan_path.parent != expected_request_folder
            or not snapshot_plan_path.name.startswith("EXECUTED_PLAN_")
            or not snapshot_plan_path.is_file()
            or sha256(snapshot_plan_path) != str(execution_snapshot.get("reference_plan_snapshot_sha256", "")).lower()
        ):
            raise StylePackError(
                f"Generated image at {output_reference} cannot be registered because its immutable executed-plan snapshot is missing, stale, or bound to another request."
            )
        # A user correction can replace the request's mutable current plan.
        # Registration continues against the immutable plan that launched this attempt.
        if not bound_plan_path.is_file() or sha256(bound_plan_path) != str(snapshot_binding.get("sha256", "")).lower():
            registration_plan_arg = str(snapshot_plan_path)
    plan: dict[str, object] | None = None
    risk_level = args.risk_level.upper() if args.risk_level else ""
    stage_id = ""
    qa_required: list[str] = []
    qa_failed: list[str] = []
    qa_results: dict[str, str] = {}
    if args.reference_plan:
        reference_plan_path, plan = validate_reference_plan_for_recording(paths, str(registration_plan_arg), args.fidelity)
        if plan.get("request_id") != request_id:
            raise StylePackError(f"Generated image at {output_reference} cannot be registered because the reference plan belongs to a different request.")
        executed_call = plan.get("execution_call")
        executed_stage = str(execution_snapshot.get("execution_stage", "")).strip()
        request_purpose = str(execution_snapshot.get("request_purpose", "")).strip().upper() or "UNSPECIFIED"
        workflow = plan.get("generation_workflow")
        workflow_mode = str(workflow.get("mode", "")).upper() if isinstance(workflow, dict) else ""
        plan_purpose = str(plan.get("generation_purpose", "")).strip().upper() or "UNSPECIFIED"
        guard_stage = str(execution_snapshot.get("guard_stage", execution_snapshot.get("stage", ""))).strip()
        if (
            not isinstance(executed_call, dict)
            or workflow_mode not in {"SINGLE_PASS", "MULTI_STAGE"}
            or plan_purpose != request_purpose
            or str(executed_call.get("stage_id", "")).upper() != executed_stage.upper()
            or not executed_stage
            or execution_snapshot.get("execution_call") != executed_call
            or (
                guard_stage.upper() != executed_stage.upper()
                and not (request_purpose == "SCENE" and workflow_mode == "SINGLE_PASS" and guard_stage.upper() == "SCENE" and executed_stage.upper() == "SINGLE_PASS")
            )
        ):
            raise StylePackError(
                f"Generated image at {output_reference} cannot be registered because the reference plan purpose or executable stage does not match the durable attempt snapshot."
            )
        call_json = json.dumps(executed_call, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if hashlib.sha256(call_json.encode("utf-8")).hexdigest() != execution_snapshot.get("execution_call_sha256"):
            raise StylePackError(
                f"Generated image at {output_reference} cannot be registered because the plan execution call differs from the durable attempt snapshot."
            )
        # A stale caller-supplied stage label is recoverable only because the
        # exact executed stage above is bound to this request, attempt and plan.
        # Use that stage for QA; never reinterpret SCENE/SINGLE_PASS globally.
        args.stage_id = executed_stage if workflow_mode == "MULTI_STAGE" else ""
        if str(args.character_id).upper() != str(plan.get("character_id", "")).upper():
            raise StylePackError(f"Generated image at {output_reference} cannot be registered because --character-id does not match the prepared plan.")
        binding = active_attempt.get("reference_binding", {})
        if (
            Path(str(binding.get("path", ""))).resolve() != Path(str(snapshot_binding.get("path", ""))).resolve()
            or binding.get("sha256") != snapshot_binding.get("sha256")
            or str(binding.get("stage", "")).upper() != str(active_attempt.get("stage", "")).upper()
        ):
            raise StylePackError(f"Generated image at {output_reference} cannot be registered because the active attempt is not bound to this reference plan snapshot.")
        if plan.get("execution_call", {}).get("stage_id", "").upper() != str(execution_snapshot.get("execution_stage", "")).upper():
            raise StylePackError(f"Generated image at {output_reference} cannot be registered because the attempt snapshot stage does not match the executable call.")
        planned_risk = str(plan.get("risk_assessment", {}).get("generation_risk", "")).upper()
        if risk_level and risk_level != planned_risk:
            raise StylePackError(f"Recorded risk {risk_level} does not match prepared plan risk {planned_risk}.")
        risk_level = planned_risk
        try:
            review_arg = str(getattr(args, "visual_review_json", "") or "").strip()
            if not review_arg:
                raise StylePackError("Every project-plan record-generation call requires --visual-review-json.")
            review_path = Path(review_arg).expanduser()
            if not review_path.is_absolute():
                review_path = paths.workspace / review_path
            visual_review = validate_visual_review(
                review_path.resolve(),
                request_id=request_id,
                attempt_id=args.attempt_id,
                task_revision=int(active_attempt.get("task_revision", -1)),
                output=image,
                execution_snapshot=execution_snapshot,
                plan=plan,
                stage_id=executed_stage.upper(),
            )
            args.visual_anatomy_status = visual_review["anatomy_review"]["status"]
            args.visual_defects_status = visual_review["visible_defect_review"]["status"]
            args.visual_prompt_status = visual_review["prompt_adherence"]["status"]
            qa_failed, stage_id, qa_required, qa_results = evaluate_generation_qa(plan, args, include_results=True)
            if stage_id.upper() != str(execution_snapshot.get("execution_stage", "")).upper():
                raise StylePackError(
                    f"Recorded --stage-id {stage_id} does not match the executable stage in the attempt snapshot {execution_snapshot.get('execution_stage', '')}."
                )
            validate_prior_stages(paths, plan, request_id, stage_id)
        except StylePackError:
            raise
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
    else:
        raise StylePackError(f"Generated image at {output_reference} cannot be registered because every fidelity requires --reference-plan for identity, call, and QA binding.")
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
    prior_record = next(
        (
            row for row in read_csv(paths.generation_manifest)
            if row.get("request_id") == request_id
            and f"[ATTEMPT_ID={args.attempt_id}]" in row.get("notes", "")
        ),
        None,
    ) if paths.generation_manifest.is_file() else None
    if prior_record:
        prior_file = Path(prior_record.get("style_file", ""))
        if (
            not prior_file.is_file()
            or sha256(prior_file) != sha256(image)
            or prior_record.get("reference_plan") != str(reference_plan_path.resolve())
            or prior_record.get("status", "").upper() not in {status, "REJECTED"}
            or f"[STAGE_ID={stage_id}]" not in prior_record.get("notes", "")
        ):
            raise StylePackError("This available attempt already has a different or unverifiable generation record.")
        print(f"GENERATION_ID={prior_record.get('generation_id', '')}")
        print(f"ARCHIVE_FILE={prior_record.get('archive_file', '')}")
        print(f"STYLE_FILE={prior_file}")
        print(f"RISK_LEVEL={prior_record.get('risk_level', '')}")
        print(f"STAGE_ID={stage_id}")
        print(f"STATUS={prior_record.get('status', '')}")
        print("UNCHANGED_RETRY=true")
        return
    # No archive or stored derivative is written until request/attempt/plan,
    # stage, character, risk, QA and idempotency validation has completed.
    # QA-failed output still flows through the explicit REJECTED record path.
    existing_marker = re.search(r"\[(D(?:[1-9]|10))\]\s*$", args.description, flags=re.IGNORECASE)
    if existing_marker and existing_marker.group(1).upper() != risk_level:
        raise StylePackError("Description risk marker conflicts with --risk-level.")
    limb_qa_evidence = str(getattr(args, "limb_qa_evidence", "") or "").strip()
    anthropometric_violations: list[str] = []
    if plan and limb_qa_evidence and int(plan.get("semantic_qa_schema", 0) or 0) >= 4:
        anthropometric_violations = anthropometric_qa_violations(limb_qa_evidence, plan)
    archive_file = archive_file or archive_generation(paths, image, args.description)
    output_reference = archive_file
    style_file = copy_unique(image, pending_root / image.name)
    reference_plan = str(reference_plan_path) if reference_plan_path else ""
    qa_evidence = ""
    qa_binding: dict[str, str] = {}
    qa_note = ""
    if plan:
        qa_note = (
            f"[ATTEMPT_ID={args.attempt_id}] [TASK_REVISION={int(active_attempt.get('task_revision', -1))}] "
            f"[STAGE_ID={stage_id}] [QA_REQUIRED={','.join(qa_required)}]"
        )
        if qa_failed:
            qa_note += f" [AUTO_REJECT_QA={','.join(qa_failed)}]"
        elif status in {"TEST", "STAGING"}:
            qa_note += f" [QA_OUTPUT_SHA256={sha256(image)}]"
        if limb_qa_evidence:
            qa_note += f" [LIMB_QA={limb_qa_evidence}]"
            if anthropometric_violations:
                qa_note += f" [ANTHROPOMETRIC_VIOLATIONS={' | '.join(anthropometric_violations)}]"
    combined_notes = " ".join(part for part in (args.notes.strip(), qa_note) if part)
    if plan:
        # The receipt records both successful and rejected visual review so a
        # correction can use the exact findings. Only a clean receipt can
        # establish passed QA in downstream promotion gates.
        qa_evidence_path = write_generation_qa_evidence(
            style_file,
            Path(str(execution_snapshot["reference_plan_snapshot_path"])),
            stage_id=stage_id,
            qa_required=qa_required,
            qa_results=qa_results,
            status=status,
            visual_review=visual_review,
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
    if not already_visible:
        try:
            execution_checkpoint(
                execution_guard_path,
                event="VISIBLE_RESULT",
                summary=f"Generated image {new_id} was archived and recorded with status {status}.",
                attempt_id=args.attempt_id,
                result_status=status,
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


def command_confirm_profile(args: argparse.Namespace) -> None:
    """Publish agent-applied, user-confirmed facts and approved asset defaults."""
    paths = make_paths(args.workspace, args.style_name)
    profile = character_folder(paths, args.character_id.upper()) / "CHARACTER_PROFILE.yaml"
    _approved_profile_identity(paths, profile, args.character_id.upper())
    if args.patch_file and args.candidate_yaml:
        raise StylePackError("Choose one profile content source: --patch-file or --candidate-yaml.")
    if args.patch_file:
        patch = json.loads(Path(args.patch_file).read_text(encoding="utf-8"))
    elif args.candidate_yaml:
        import yaml
        candidate = yaml.safe_load(Path(args.candidate_yaml).read_text(encoding="utf-8"))
        current = load_effective(profile)["profile"]
        if not isinstance(candidate, dict) or any(candidate.get(key) != current.get(key) for key in PROFILE_STRUCTURAL):
            raise StylePackError("Candidate YAML cannot change canonical identity or structural fields.")
        patch = {key: value for key, value in candidate.items() if key not in PROFILE_STRUCTURAL}
    else:
        patch = {}
    if not isinstance(patch, dict):
        raise StylePackError("Confirmed profile patch must be a JSON object.")
    evidence = approved_profile_asset_evidence(paths, args.character_id.upper(), profile)
    approved, _ = approved_character_role_assets(paths, args.character_id.upper(), profile)
    catalog = {(item["role"], Path(item["path"]).resolve()) for item in approved}
    role_names = {"WARDROBE": "wardrobe", "ACCESSORY": "accessory", "FACE_VARIANT": "face_variant", "BODY_VARIANT": "body_variant"}
    changes: dict[str, list[str]] = {}
    for item in args.active_asset:
        role, sep, value = item.partition("=")
        role = role.upper()
        if not sep or role not in role_names:
            raise StylePackError("--active-asset requires WARDROBE|ACCESSORY|FACE_VARIANT|BODY_VARIANT=approved-path.")
        path = resolve_existing_file(value, paths).resolve()
        if (role, path) not in catalog:
            raise StylePackError(f"Profile asset is not a currently approved role asset: {role} {path}")
        changes.setdefault(role_names[role], []).append(str(path))
    try:
        state = confirm_profile_state(profile, patch=patch, active_changes=changes,
                                      expected_revision=args.expected_revision, operation_id=args.operation_id,
                                      user_confirmation=args.confirmation_quote, alternative_only=args.alternative_only,
                                      replace_facts=bool(args.candidate_yaml), approved_assets=evidence)
    except (ProfileStateError, OSError) as error:
        raise StylePackError(str(error)) from error
    for role, values in changes.items():
        field = {"wardrobe": "wardrobe_references", "accessory": "accessory_references",
                 "face_variant": "face_variant_references", "body_variant": "body_variant_references"}[role]
        for value in values:
            sync_character_profile_asset(profile, field, Path(value))
    print(f"PROFILE_REVISION={state['revision']}")
    print(f"PROFILE_SHA256={profile_digest(state)}")
    print("STATUS=CONFIRMED_PROFILE_ACTIVE")


def approved_profile_asset_evidence(paths: StylePaths, character_id: str, profile: Path) -> dict[str, list[dict[str, str]]]:
    """Translate only registry identity and approved role-catalog records into hash evidence."""
    rows = [row for row in read_csv(paths.character_registry) if row.get("character_id", "").upper() == character_id
            and row.get("status", "").upper() == "APPROVED"]
    if len(rows) != 1:
        raise StylePackError("Profile hash evidence requires exactly one approved character registry row.")
    result: dict[str, list[dict[str, str]]] = {role: [] for role in ("identity", "wardrobe", "accessory", "face_variant", "body_variant")}
    registry = rows[0]
    registered_profile = Path(registry.get("profile_path", ""))
    if not registered_profile.is_absolute():
        registered_profile = paths.generations / registered_profile
    if registered_profile.resolve() != profile.resolve():
        raise StylePackError("Profile hash evidence registry path differs from the approved character profile.")
    first_publication = (profile.parent / "CONFIRMED_PROFILE" / "REQUIRED.json").is_file() and not (profile.parent / "CONFIRMED_PROFILE" / "ACTIVE.json").is_file()
    if first_publication:
        import yaml
        raw = yaml.safe_load(profile.read_text(encoding="utf-8-sig"))
        base = Path(registry.get("approved_base", ""))
        if not base.is_absolute():
            base = paths.generations / base
        base = base.resolve()
        manifest = newest_matching_generation(paths, base) if base.is_file() else None
        if (not isinstance(raw, dict) or raw.get("character_id") != character_id or raw.get("status") != "APPROVED"
                or not manifest or manifest.get("status", "").upper() != "APPROVED_CHARACTER_BASE"
                or manifest.get("character_id", "").upper() != character_id
                or Path(manifest.get("style_file", "")).resolve() != base or not generation_has_passed_qa(manifest)):
            raise StylePackError("First profile publication requires its exact approved base manifest and profile identity.")
        identity_paths = {base}
        identity_paths.update(_character_registry_paths(paths, registry.get("face_references", "")))
        identity_paths.update(_character_registry_paths(paths, registry.get("body_references", "")))
        for value in [raw.get("approved_base", ""), *(raw.get("character_face_references") or []),
                      *(raw.get("character_body_references") or []), *(raw.get("canonical_views") or {}).values()]:
            if isinstance(value, str) and value.strip() and not value.startswith("{{"):
                candidate = (profile.parent / value).resolve()
                if candidate not in identity_paths:
                    raise StylePackError(f"Unregistered canonical identity reference cannot be confirmed: {candidate}")
        for path in sorted(identity_paths):
            if not path.is_file():
                raise StylePackError(f"Approved canonical identity file is missing: {path}")
            result["identity"].append({"path": str(path), "sha256": sha256(path)})
        role_specs = {"wardrobe": ("wardrobe_references", "03_WARDROBE"),
                      "accessory": ("accessory_references", "04_ACCESSORIES")}
        for role, (field, subfolder) in role_specs.items():
            expected_root = (profile.parent / "03_CHARACTER_REFERENCES" / subfolder).resolve()
            for value in raw.get(field) or []:
                path = (profile.parent / str(value)).resolve()
                if not path.is_file() or not is_relative_to(path, expected_root):
                    raise StylePackError(f"Initial approved {role} must be inside its exact role folder: {path}")
                result[role].append({"path": str(path), "sha256": sha256(path)})
        return result
    for value, role in [(registry.get("approved_base", ""), "BASE"),
                        *[(item, "FACE") for item in _character_registry_paths(paths, registry.get("face_references", ""))],
                        *[(item, "BODY") for item in _character_registry_paths(paths, registry.get("body_references", ""))]]:
        if not value:
            continue
        path = Path(value)
        if not path.is_absolute():
            path = paths.generations / path
        path = path.resolve()
        require_approved_character_asset_role(paths, path, character_id, role, "CONFIRMED_IDENTITY")
        result["identity"].append({"path": str(path), "sha256": sha256(path)})
    role_map = {"WARDROBE": "wardrobe", "ACCESSORY": "accessory", "FACE_VARIANT": "face_variant", "BODY_VARIANT": "body_variant"}
    assets, _ = approved_character_role_assets(paths, character_id, profile)
    for row in assets:
        role = role_map.get(row["role"])
        if role:
            result[role].append({"path": str(Path(row["path"]).resolve()), "sha256": row["sha256"]})
    return result


def effective_character_profile(paths: StylePaths, character_id: str) -> dict[str, object]:
    profile_path = character_folder(paths, character_id) / "CHARACTER_PROFILE.yaml"
    _approved_profile_identity(paths, profile_path, character_id)
    state = load_effective(profile_path)
    role_assets, _ = approved_character_role_assets(paths, character_id, profile_path)
    approved = {(row["role"], str(Path(row["path"]).resolve())): row for row in role_assets}
    role_names = {"wardrobe": "WARDROBE", "accessory": "ACCESSORY", "face_variant": "FACE_VARIANT", "body_variant": "BODY_VARIANT"}
    active: dict[str, list[dict[str, str]]] = {}
    for role, catalog_role in role_names.items():
        records = []
        pinned = {row["path"]: row["sha256"] for row in state.get("asset_bindings", {}).get(role, [])}
        for value in state["active_assets"].get(role, []):
            path = str(Path(value).resolve())
            record = approved.get((catalog_role, path))
            if not record:
                raise StylePackError(f"Active confirmed {role} asset lacks valid approved provenance: {path}")
            asset_hash = pinned.get(path) if state["revision"] else record["sha256"]
            if asset_hash != record["sha256"]:
                raise StylePackError(f"Confirmed {role} asset differs from its approved published bytes: {path}")
            records.append({"path": path, "sha256": asset_hash, "role": catalog_role,
                            "generation_id": record.get("generation_id", "")})
        active[role] = records
    return {"profile_path": str(profile_path), "revision": state["revision"],
            "sha256": profile_digest(state), "facts": text_facts(state["profile"], legacy=state["revision"] == 0),
            "facts_block": facts_block(state), "active_assets": active,
            "identity_bindings": state.get("identity_bindings"),
            "asset_bindings": state.get("asset_bindings"),
            "alternatives": state.get("alternatives"),
            "confirmation": state["confirmation"]}


def apply_confirmed_defaults(paths: StylePaths, args: argparse.Namespace, character_id: str,
                             confirmed: dict[str, object], overrides: set[str]) -> dict[str, list[dict[str, str]]]:
    """Fill omitted scene slots from active defaults without changing state."""
    suppressed = {str(role).upper() for role in getattr(args, "suppress_profile_default", [])}
    unknown = suppressed - {"WARDROBE", "ACCESSORY", "FACE_VARIANT", "BODY_VARIANT"}
    if unknown:
        raise StylePackError("Unknown profile default suppression: " + ", ".join(sorted(unknown)))
    active = confirmed["active_assets"]
    applied: dict[str, list[dict[str, str]]] = {role: [] for role in ("wardrobe", "accessory", "face_variant", "body_variant")}
    if not args.clothes_reference and "WARDROBE" not in suppressed and "CLOTHES" not in overrides and active["wardrobe"]:
        args.clothes_reference = active["wardrobe"][0]["path"]
        applied["wardrobe"] = list(active["wardrobe"])
    for field, role, key, override_role in (("accessory_reference", "ACCESSORY", "accessory", "ACCESSORY"),
                                            ("face_variant_reference", "FACE_VARIANT", "face_variant", "FACE"),
                                            ("body_variant_reference", "BODY_VARIANT", "body_variant", "BODY")):
        if not getattr(args, field) and role not in suppressed and override_role not in overrides:
            setattr(args, field, [row["path"] for row in active[key]])
            applied[key] = list(active[key])
    registry_rows = [row for row in read_csv(paths.character_registry) if row.get("character_id", "").upper() == character_id and row.get("status", "").upper() == "APPROVED"]
    if len(registry_rows) != 1:
        raise StylePackError("Confirmed profile requires exactly one approved registry row.")
    if not args.character_assembly:
        args.character_assembly = registry_rows[0]["approved_base"]
    profile_path = Path(confirmed["profile_path"])
    canonical = load_effective(profile_path)["profile"].get("canonical_views", {})
    strict = args.character_reference_mode == "IDENTITY_STRICT" or (args.character_reference_mode == "AUTO" and args.shot_complexity.upper() == "COMPLEX")
    if strict and not args.primary_face and canonical.get("face"):
        args.primary_face = str((profile_path.parent / canonical["face"]).resolve())
    if args.selected_body_view != "ASSEMBLY" and not args.body_reference:
        key = "physique_" + args.selected_body_view.lower()
        if canonical.get(key):
            args.body_reference = str((profile_path.parent / canonical[key]).resolve())
    return applied


def assert_profile_plan_current(paths: StylePaths, plan: dict[str, object]) -> None:
    if str(plan.get("generation_purpose", "")).upper() != "SCENE" or not re.fullmatch(r"CHAR_\d+", str(plan.get("character_id", ""))):
        return
    character_id = str(plan["character_id"])
    profile_path = character_folder(paths, character_id) / "CHARACTER_PROFILE.yaml"
    try:
        _approved_profile_identity(paths, profile_path, character_id)
        verify_plan_binding(profile_path, character_id, plan, plan.get("execution_call") if isinstance(plan.get("execution_call"), dict) else None)
        current_profile = effective_character_profile(paths, character_id)
    except ProfileStateError as error:
        raise StylePackError(f"Confirmed character profile or approved asset provenance is invalid or changed since preparation: {error}") from error
    if current_profile != plan.get("confirmed_character_profile"):
        raise StylePackError("Confirmed character profile or approved asset provenance changed since preparation; prepare a new plan.")


def render_confirmed_prompt(plan: dict[str, object], prompt_text: str) -> str:
    """Materialize confirmed facts in the exact prompt before risk assessment."""
    confirmed = plan.get("confirmed_character_profile")
    if not isinstance(confirmed, dict):
        return prompt_text
    block = str(confirmed.get("facts_block", ""))
    if "CONFIRMED_CHARACTER_DATA" in prompt_text and block not in prompt_text:
        raise StylePackError("Caller-supplied confirmed character facts differ from the active prepared profile.")
    return prompt_text if not block or block in prompt_text else prompt_text.rstrip() + "\n\n" + block


def command_approve_character(args: argparse.Namespace) -> None:
    paths = make_paths(args.workspace, args.style_name)
    ensure_generation_library(paths)
    if not args.user_approved:
        raise StylePackError("Character registration requires --user-approved after direct user confirmation.")
    request_id = safe_component(args.request_id, "request")
    pending = paths.generations / "00_PENDING" / request_id
    assert_active_request_path(pending / "EXECUTION_GUARD.json")
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
    approval_operation = f"approve-character-{request_id}"
    retry_rows = []
    for row in read_csv(paths.character_registry):
        base_candidate = Path(row.get("approved_base", ""))
        if not base_candidate.is_absolute():
            base_candidate = paths.generations / base_candidate
        profile_candidate = Path(row.get("profile_path", ""))
        if not profile_candidate.is_absolute():
            profile_candidate = paths.generations / profile_candidate
        marker = profile_candidate.parent / "CONFIRMED_PROFILE" / "REQUIRED.json"
        same_marker = marker.is_file() and json.loads(marker.read_text(encoding="utf-8")).get("operation_id") == approval_operation
        same_manifest = any(item.get("request_id") == request_id and item.get("character_id") == row.get("character_id")
                            and item.get("status") == "APPROVED_CHARACTER_BASE" for item in read_csv(paths.generation_manifest))
        if (row.get("status", "").upper() == "APPROVED" and row.get("name") == args.name
                and base_candidate.is_file() and sha256(base_candidate) == sha256(image)
                and (same_marker or same_manifest)):
            retry_rows.append((row, base_candidate.resolve()))
    if len(retry_rows) > 1:
        raise StylePackError("Ambiguous previous character approval for the same name and source image.")
    if retry_rows:
        old_row, approved_base = retry_rows[0]
        character_id = old_row["character_id"]
        profile_path = Path(old_row["profile_path"])
        if not profile_path.is_absolute():
            profile_path = paths.generations / profile_path
        profile_path = profile_path.resolve()
        require_first_publication(profile_path, approval_operation)
        previous = [row for row in read_csv(paths.generation_manifest) if row.get("request_id") == request_id
                    and row.get("character_id") == character_id and row.get("status") == "APPROVED_CHARACTER_BASE"
                    and Path(row.get("style_file", "")).resolve() == approved_base]
        if len(previous) > 1:
            raise StylePackError("Ambiguous previous base approval records.")
        if previous:
            new_id = previous[0]["generation_id"]
        else:
            archive_file = ensure_generation_archived(paths, image, f"{character_id}_{args.name}_approved_base")
            parent_generation, reference_plan, qa_evidence, qa_binding, approval_notes = approval_provenance(source_generation, args.notes, approved_base)
            new_id = append_generation(paths, request_id=request_id, character_id=character_id,
                                       status="APPROVED_CHARACTER_BASE", fidelity=args.fidelity,
                                       risk_level=args.risk_level, description=f"Approved character base: {args.name}",
                                       source_image=image, archive_file=archive_file, style_file=approved_base,
                                       parent_generation=parent_generation, reference_plan=reference_plan,
                                       qa_evidence=qa_evidence, **qa_binding, notes=approval_notes)
        try:
            confirmed = confirm_profile_state(profile_path, patch={}, active_changes={}, expected_revision=0,
                                              operation_id=approval_operation,
                                              user_confirmation=getattr(args, "approval_quote", "") or args.notes or "--user-approved",
                                              approved_assets=approved_profile_asset_evidence(paths, character_id, profile_path))
        except (ProfileStateError, OSError) as error:
            raise StylePackError(f"Retry of character approval {new_id} could not publish its profile: {error}") from error
        print(f"CHARACTER_ID={character_id}")
        print(f"CHARACTER_FOLDER={profile_path.parent}")
        print(f"CHARACTER_PROFILE={profile_path}")
        print(f"GENERATION_ID={new_id}")
        print(f"PROFILE_REVISION={confirmed['revision']}")
        print("STATUS=APPROVED_CHARACTER_REGISTERED")
        return
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
    require_first_publication(profile_path, approval_operation)

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
    try:
        confirmed = confirm_profile_state(profile_path, patch={}, active_changes={}, expected_revision=0,
                                          operation_id=approval_operation,
                                          user_confirmation=getattr(args, "approval_quote", "") or args.notes or "--user-approved",
                                          approved_assets=approved_profile_asset_evidence(paths, character_id, profile_path))
    except (ProfileStateError, OSError) as error:
        raise StylePackError(f"Character base {new_id} was recorded, but confirmed profile publication failed: {error}") from error
    print(f"CHARACTER_ID={character_id}")
    print(f"CHARACTER_FOLDER={folder}")
    print(f"CHARACTER_PROFILE={profile_path}")
    print(f"GENERATION_ID={new_id}")
    print(f"PROFILE_REVISION={confirmed['revision']}")
    print("STATUS=APPROVED_CHARACTER_REGISTERED")


def command_approve_variation(args: argparse.Namespace) -> None:
    paths = make_paths(args.workspace, args.style_name)
    ensure_generation_library(paths)
    if not args.user_approved:
        raise StylePackError("Variation approval requires --user-approved after direct confirmation.")
    if args.kind == "sketch":
        if not args.description.strip():
            raise StylePackError("Sketch approval requires --description explaining its specific usefulness.")
        if not getattr(args, "approval_quote", "").strip():
            raise StylePackError("Sketch approval requires a direct --approval-quote scoped to saving this useful sketch.")
    if args.fidelity not in {30, 50, 70, 90, 100}:
        raise StylePackError("Fidelity must be one of 30, 50, 70, 90, or 100.")
    image = resolve_existing_file(args.image, paths)
    source_generation = require_qa_passed_generation_for_approval(paths, image)
    if str(source_generation.get("character_id", "")).upper() != str(args.character_id).upper():
        raise StylePackError("Variation approval cannot relabel a QA-passed image from another character.")
    source_plan_path = Path(str(source_generation.get("reference_plan", "")))
    assert_active_request_path(source_plan_path)
    if not source_plan_path.is_file():
        raise StylePackError("Variation approval requires the source generation's validated reference plan.")
    try:
        source_plan = json.loads(source_plan_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise StylePackError(f"Cannot read the source generation's reference plan: {error}") from error
    if str(source_plan.get("character_id", "")).upper() != str(args.character_id).upper():
        raise StylePackError("Variation approval cannot use a QA-passed image whose plan identifies another character.")
    folder = character_folder(paths, args.character_id)
    profile = folder / "CHARACTER_PROFILE.yaml"
    if args.kind != "sketch" and not (folder / "CONFIRMED_PROFILE" / "ACTIVE.json").is_file():
        try:
            confirm_profile_state(profile, patch={}, active_changes={}, expected_revision=0,
                                  operation_id=f"bootstrap-{args.character_id}",
                                  user_confirmation="Existing approved registry/profile baseline",
                                  approved_assets=approved_profile_asset_evidence(paths, args.character_id.upper(), profile))
        except (ProfileStateError, OSError) as error:
            raise StylePackError(f"Cannot secure legacy profile before approval: {error}") from error
    destinations = {
        "variation": ("01_VARIATIONS", "APPROVED_VARIATION"),
        "scene": ("02_SCENES", "APPROVED_SCENE"),
        "wardrobe": ("03_CHARACTER_REFERENCES/03_WARDROBE", "APPROVED_WARDROBE"),
        "accessory": ("03_CHARACTER_REFERENCES/04_ACCESSORIES", "APPROVED_ACCESSORY"),
        "face": ("03_CHARACTER_REFERENCES/05_FACE_VARIANTS", "APPROVED_FACE_VARIANT"),
        "body": ("03_CHARACTER_REFERENCES/06_BODY_VARIANTS", "APPROVED_BODY_VARIANT"),
        "sketch": ("03_CHARACTER_REFERENCES/07_USEFUL_SKETCHES", "APPROVED_SKETCH"),
    }
    subfolder, approved_status = destinations[args.kind]
    approval_request_id = safe_component(args.request_id, "approved")
    previous = [row for row in read_csv(paths.generation_manifest) if row.get("request_id") == approval_request_id
                and row.get("character_id", "").upper() == args.character_id.upper()
                and row.get("status") == approved_status and Path(row.get("source_image", "")).resolve() == image.resolve()
                and row.get("description", "").startswith(args.description)]
    if len(previous) > 1:
        raise StylePackError("Ambiguous prior approval rows; resolve provenance before retrying.")
    if previous:
        approved = Path(previous[0]["style_file"]).resolve()
        if not approved.is_file() or sha256(approved) != sha256(image):
            raise StylePackError("Prior approved asset is missing or differs from its source; cannot retry publication.")
        new_id = previous[0]["generation_id"]
    else:
        archive_file = ensure_generation_archived(paths, image, f"{args.character_id}_{args.kind}_{args.description}")
        (folder / subfolder).mkdir(parents=True, exist_ok=True)
        approved = copy_unique(image, folder / subfolder / image.name)
        source_parent, source_plan, qa_evidence, qa_binding, approval_notes = approval_provenance(source_generation, args.notes, approved)
        if args.kind == "sketch":
            approval_notes += f"; sketch_approval_quote={args.approval_quote.strip()}"
        new_id = append_generation(
            paths,
            request_id=approval_request_id,
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
    role_field = {
        "wardrobe": "wardrobe_references",
        "accessory": "accessory_references",
        "face": "face_variant_references",
        "body": "body_variant_references",
    }.get(args.kind)
    if role_field:
        state = load_effective(profile)
        role = {"wardrobe": "wardrobe", "accessory": "accessory", "face": "face_variant", "body": "body_variant"}[args.kind]
        try:
            confirm_profile_state(profile, patch={}, active_changes={role: [str(approved)]},
                                  expected_revision=state["revision"], operation_id=f"approval-{new_id}",
                                  user_confirmation=getattr(args, "approval_quote", "") or args.notes or "--user-approved",
                                  alternative_only=getattr(args, "alternative_only", False),
                                  approved_assets=approved_profile_asset_evidence(paths, args.character_id.upper(), profile))
        except (ProfileStateError, OSError) as error:
            raise StylePackError(f"Approved asset {new_id} is recorded, but profile publication failed; retry the same approval: {error}") from error
        sync_character_profile_asset(profile, role_field, approved)
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

    character_lookup_parser = subparsers.add_parser(
        "resolve-character",
        help="Resolve a named character from approved registries and identity assets only.",
    )
    character_lookup_parser.add_argument("--workspace", type=Path, default=DEFAULT_WORKSPACE)
    character_lookup_parser.add_argument("--name", required=True, help="Exact approved character name or CHAR_NNN ID.")
    character_lookup_parser.add_argument("--json", action="store_true")
    character_lookup_parser.set_defaults(handler=command_resolve_character)

    startup_menu_parser = subparsers.add_parser(
        "startup-menu-template",
        help="Build the canonical profile-bound 90/90/70 chooser with exact machine mappings.",
    )
    startup_menu_parser.add_argument("--style-name", required=True, help="Exact style from the approved character profile.")
    startup_menu_parser.add_argument("--character-id", required=True, help="Approved CHAR_NNN identity.")
    startup_menu_parser.add_argument("--character-name", required=True, help="Approved character display name.")
    startup_menu_parser.add_argument("--generation-purpose", choices=("SCENE", "CHARACTER_BASE"), default="SCENE")
    startup_menu_parser.add_argument("--json", action="store_true", help="Return machine-readable menu text and exact parameter mappings.")
    startup_menu_parser.set_defaults(handler=command_startup_menu_template)

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
    body_context_parser.add_argument("--style-name", default="", help="Style whose approved character profile supplies compatibility metadata.")
    body_context_parser.add_argument("--character-id", default="NONE")
    body_context_parser.add_argument("--character-profile", default="")
    body_context_parser.add_argument("--target-anatomy", default="UNKNOWN")
    body_context_parser.add_argument("--target-anatomy-evidence", default="UNKNOWN")
    body_context_parser.add_argument("--target-prompt", default="", help="Exact current user prompt; only explicit target wording can supply anatomy provenance.")
    body_context_parser.add_argument("--target-name", default="", help="Name used to bind a gendered pronoun in --target-prompt to the requested subject.")
    body_context_parser.add_argument("--target-visible-presentation", default="UNKNOWN")
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
    classify_parser.add_argument("--anatomy-compatibility", default="UNKNOWN", help="Reviewed anatomy role compatibility, e.g. MALE_ANATOMY; FEMALE_ANATOMY; ANDROGYNOUS_ANATOMY.")
    classify_parser.add_argument("--anatomy-evidence-source", default="UNKNOWN", help="Review evidence supporting anatomy compatibility metadata.")
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
        choices=("DIRECT_CONFIRMATION", "NEW", "REUSE", "USER_CONFIRMATION"),
        default="NEW",
        help="Record a newly shown startup profile menu; explicitly set --startup-menu-surface to the control actually presented. Use USER_CONFIRMATION with TEXT_NUMBERED_MENU only for a visible text fallback. DIRECT_CONFIRMATION requires fully confirmed current-chat parameters.",
    )
    prepare_parser.add_argument(
        "--reuse-startup-from",
        default="",
        help="Previous same-chat REFERENCE_PLAN.json. Required for REUSE.",
    )
    prepare_parser.add_argument(
        "--startup-menu-surface",
        choices=("NATIVE_CONTEXT_MENU", "TEXT_NUMBERED_MENU"),
        default="",
        help="Record the native menu actually shown; explicitly select TEXT_NUMBERED_MENU only for the visible text fallback.",
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
    prepare_parser.add_argument("--reuse-chat-id", default="", help="Current chat identifier to verify same-chat reuse.")
    prepare_parser.add_argument("--reuse-message-id", default="", help="Original confirmed message identifier to verify reuse.")
    prepare_parser.add_argument("--confirmed-chat-id", default="", help="Current chat identifier supporting a direct profile or menu selection.")
    prepare_parser.add_argument("--confirmed-message-id", default="", help="Message identifier containing the direct profile or menu selection.")
    prepare_parser.add_argument("--confirmed-parameters-user-quote", default="", help="Exact user wording supporting directly confirmed fidelity.")
    prepare_parser.add_argument("--confirmed-body-library-user-quote", default="", help="Optional separate current-chat quote explicitly selecting or declining BODY_REFERENCE_LIBRARY.")
    prepare_parser.add_argument("--confirmed-body-library-chat-id", default="", help="Chat id for a separate BODY_REFERENCE_LIBRARY confirmation; must match the fidelity confirmation chat.")
    prepare_parser.add_argument("--confirmed-body-library-message-id", default="", help="Message id for a separate BODY_REFERENCE_LIBRARY confirmation.")
    prepare_parser.add_argument(
        "--startup-option",
        action="append",
        default=[],
        help="Visible profile as OPTION_N=description; include the explicit fidelity and BODY_REFERENCE_LIBRARY decision for all three profiles.",
    )
    prepare_parser.add_argument("--fidelity", type=int, required=True, choices=(30, 50, 70, 90, 100))
    prepare_parser.add_argument(
        "--custom-parameters-user-quote",
        default="",
        help="Required only for CUSTOM; the user's complete one-message description, including fidelity. Follow up only for genuinely missing required information.",
    )
    prepare_parser.add_argument(
        "--risk-assessment",
        default="",
        help="Risk reports are bound to exact executable calls by prepare-call.",
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
    prepare_parser.add_argument("--character-profile", default="", help="Optional explicit profile for NEW or unregistered target identity metadata.")
    prepare_parser.add_argument("--target-anatomy", default="UNKNOWN", help="Explicit target anatomy for compatibility checks: MALE_ANATOMY, FEMALE_ANATOMY, or ANDROGYNOUS_ANATOMY.")
    prepare_parser.add_argument("--target-anatomy-evidence", default="UNKNOWN", help="Review source for explicitly supplied target anatomy metadata.")
    prepare_parser.add_argument("--target-prompt", default="", help="Exact current user prompt for explicit target anatomy parsing when no approved profile supplies it.")
    prepare_parser.add_argument("--target-name", default="", help="Target name used to bind explicit pronouns in --target-prompt.")
    prepare_parser.add_argument("--target-visible-presentation", default="UNKNOWN")
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
        help="AUTO uses one pass when possible; any SCENE that overflows attachments requires an explicitly requested MULTI_STAGE workflow with user authorization.",
    )
    prepare_parser.add_argument("--style-reference", action="append", default=[], help="Local overall rendering reference; repeat if needed.")
    prepare_parser.add_argument("--primary-face", default="")
    prepare_parser.add_argument("--supporting-face", default="")
    prepare_parser.add_argument("--expression-reference", default="")
    prepare_parser.add_argument(
        "--reviewed",
        action="append",
        default=[],
        help="Legacy reported pool-review counts as ROLE=COUNT; these counts do not satisfy selected-source review.",
    )
    prepare_parser.add_argument("--face-candidates-reviewed", type=int, default=0, help="Deprecated alias for --reviewed FACE=COUNT.")
    prepare_parser.add_argument(
        "--reviewed-source",
        action="append",
        default=[],
        help="Attest one exact selected-source visual review as a JSON object with role, slot_role, path, view, outcome, applicability, findings, and limitations; repeat per role and slot.",
    )
    prepare_parser.add_argument("--face-selection-evidence", default="")
    prepare_parser.add_argument("--body-reference", default="")
    prepare_parser.add_argument("--pose-reference", default="")
    prepare_parser.add_argument("--clothes-reference", default="")
    prepare_parser.add_argument("--accessory-reference", action="append", default=[], help="Exact approved character accessory reference; repeat to attach multiple role-specific accessories.")
    prepare_parser.add_argument("--face-variant-reference", action="append", default=[], help="Exact approved optional face variant; repeat to attach separate approved face-role variants.")
    prepare_parser.add_argument("--body-variant-reference", action="append", default=[], help="Exact approved optional body variant; repeat to attach separate approved body-role variants.")
    prepare_parser.add_argument("--suppress-profile-default", action="append", default=[], help="Request-scoped suppression of WARDROBE, ACCESSORY, FACE_VARIANT, or BODY_VARIANT defaults.")
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
        choices=("NOT_SELECTED", "SELECTED", "DECLINED"),
        default="NOT_SELECTED",
        help="Whether optional shared style-neutral body/staging references are used; NOT_SELECTED makes no claim of user refusal.",
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
    prepare_parser.add_argument("--override", action="append", default=[], choices=(*PLAN_CATEGORIES, "ACCESSORY"))
    prepare_parser.add_argument("--notes", default="")
    prepare_parser.set_defaults(handler=command_prepare_generation)

    prepare_call_parser = subparsers.add_parser(
        "prepare-call",
        help="Resolve one exact stage's references, validate its prompt/risk bindings, and checkpoint readiness.",
    )
    add_common_style_arguments(prepare_call_parser)
    prepare_call_parser.add_argument("--request-id", required=True)
    prepare_call_parser.add_argument("--stage-id", default="", help="Exact canonical stage ID; defaults to the first planned stage or SINGLE_PASS.")
    prompt_group = prepare_call_parser.add_mutually_exclusive_group(required=True)
    prompt_group.add_argument("--prompt-text", default="", help="Exact user-facing prompt text for this executable call.")
    prompt_group.add_argument("--prompt-text-file", default="", help="UTF-8 file containing the exact prompt text.")
    prepare_call_parser.add_argument("--risk-assessment", required=True, help="Input-bound report from generation_risk_assessor.py for this exact prompt and resolved slot set.")
    prepare_call_parser.add_argument("--user-selections-json", default="", help="Optional explicit chat-sourced selections; must equal resolved menu selections when present. Required for legacy plans without a complete menu mapping.")
    prepare_call_parser.add_argument("--user-reference-evidence-json", default="", help="User attachment provenance list: path, sha256, source_path, chat_id, message_id. Required for user-reference policies.")
    prepare_call_parser.set_defaults(handler=command_prepare_call)

    resolve_call_parser = subparsers.add_parser(
        "resolve-call",
        help="Resolve one stage's concrete reference paths and hashes before running its exact risk assessment.",
    )
    add_common_style_arguments(resolve_call_parser)
    resolve_call_parser.add_argument("--request-id", required=True)
    resolve_call_parser.add_argument("--stage-id", default="", help="Exact canonical stage ID; defaults to the first planned stage or SINGLE_PASS.")
    resolve_call_parser.set_defaults(handler=command_resolve_call)

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
        help="Required only when a legacy-compatible plan has no bound D1-D10 assessment; prepared plans supply their exact-call risk.",
    )
    record_parser.add_argument("--status", default="TEST", choices=("STAGING", "TEST", "REJECTED"))
    record_parser.add_argument("--character-id", required=True, help="Must match the immutable request plan character_id, including NEW or NONE.")
    record_parser.add_argument("--attempt-id", required=True, help="Active attempt id returned by EXECUTION_STARTED for this exact plan/stage call.")
    record_parser.add_argument("--parent-generation", default="")
    record_parser.add_argument("--reference-plan", required=True, help="Executable plan prepared by prepare-call for this exact request.")
    record_parser.add_argument("--stage-id", default="", help="Required for a MULTI_STAGE plan, for example 02_BODY_POSE.")
    record_parser.add_argument("--visual-review-json", required=True, help="Required full-resolution visual review bound to request, attempt, output, prompt, and executed plan hashes.")
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
    record_parser.add_argument("--qa-objects-action", choices=("PASS", "FAIL", "NOT_CHECKED"), default="NOT_CHECKED")
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
    character_parser.add_argument("--approval-quote", default="", help="Exact user confirmation for the initial confirmed profile revision.")
    character_parser.add_argument("--user-approved", action="store_true", help="Confirms direct user approval.")
    character_parser.set_defaults(handler=command_approve_character)

    variation_parser = subparsers.add_parser("approve-variation", help="Store an approved variation inside an existing character folder.")
    add_common_style_arguments(variation_parser)
    variation_parser.add_argument("--image", required=True)
    variation_parser.add_argument("--character-id", required=True)
    variation_parser.add_argument("--request-id", required=True)
    variation_parser.add_argument("--kind", choices=("variation", "scene", "wardrobe", "accessory", "face", "body", "sketch"), default="variation")
    variation_parser.add_argument("--description", required=True)
    variation_parser.add_argument("--fidelity", type=int, default=90)
    variation_parser.add_argument("--risk-level", required=True, choices=tuple(f"D{index}" for index in range(1, 11)))
    variation_parser.add_argument("--parent-generation", default="")
    variation_parser.add_argument("--notes", default="")
    variation_parser.add_argument("--approval-quote", default="", help="Exact user confirmation for this role asset.")
    variation_parser.add_argument("--user-approved", action="store_true")
    variation_parser.add_argument("--alternative-only", action="store_true", help="Save a confirmed role asset as an alternative without activating it by default.")
    variation_parser.set_defaults(handler=command_approve_variation)

    confirm_parser = subparsers.add_parser("confirm-profile", help="Publish one directly confirmed character profile revision.")
    add_common_style_arguments(confirm_parser)
    confirm_parser.add_argument("--character-id", required=True)
    confirm_parser.add_argument("--patch-file", default="", help="JSON object of nested generic textual profile changes.")
    confirm_parser.add_argument("--candidate-yaml", default="", help="Candidate YAML containing new facts; canonical fields must match the active revision.")
    confirm_parser.add_argument("--active-asset", action="append", default=[], help="Approved ROLE=path; repeat for additive accessories.")
    confirm_parser.add_argument("--expected-revision", type=int, required=True)
    confirm_parser.add_argument("--operation-id", required=True)
    confirm_parser.add_argument("--confirmation-quote", required=True, help="Direct user confirmation provenance.")
    confirm_parser.add_argument("--alternative-only", action="store_true")
    confirm_parser.set_defaults(handler=command_confirm_profile)

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
    except (StylePackError, ProfileStateError) as error:
        print(f"ERROR={error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
