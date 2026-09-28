"""Confirmed, revisioned character facts and role-specific asset defaults.

The YAML profile remains editable source material. Once confirmed, only the
published snapshot is effective. Orphan revisions are harmless after a failed
publication because ACTIVE.json is the sole activation point.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


class ProfileStateError(ValueError):
    pass


STRUCTURAL = frozenset({"schema_version", "style_name", "character_id", "name", "status", "created_at", "approved_base", "last_approved_frame", "canonical_views", "character_face_references", "character_body_references", "wardrobe_references", "accessory_references", "face_variant_references", "body_variant_references"})
TEMPLATE_PLACEHOLDERS = frozenset({
    "Describe approved eyes, nose, lips, cheeks, jaw, and face proportions before the next generation.",
    "Describe approved height, build, torso, chest, waist, hips, limbs, and other permanent proportions before the next generation.",
    "Record the approved permanent skin tone.",
    "Record permanent hair, marks, or other identity features.",
    "Complete all identity descriptions from the approved sources before further generation.",
    "Use the current approved prompt's fully opaque safe coverage; do not infer a garment or coverage design from this profile.",
    "Minimal opaque matte tape over nipples, genitals, and anus where visible only.",
    "Clothing, accessories, expression, pose, scene, and lighting only when requested.",
    "Confirmed clothing and accessory defaults apply in later scenes; scene-only changes require a request.",
    "Do not replace face identity with FACE_CORE or FACE_EXPRESSION.",
    "Do not replace body identity with BODY_CORE or POSE_CORE.",
})
ASSET_ROLES = ("wardrobe", "accessory", "face_variant", "body_variant")
PROFILE_FIELDS = {"wardrobe": "wardrobe_references", "accessory": "accessory_references", "face_variant": "face_variant_references", "body_variant": "body_variant_references"}


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _json_data(value: Any) -> None:
    def check(item: Any) -> None:
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                raise ProfileStateError("Profile JSON object keys must be strings.")
            for child in item.values():
                check(child)
        elif isinstance(item, list):
            for child in item:
                check(child)
        elif item is None or isinstance(item, (str, int, float, bool)):
            return
        else:
            raise ProfileStateError("Profile data must be JSON values only.")
    check(value)
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ProfileStateError(f"Profile data is not finite JSON: {error}") from error


def _file_hash(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _paths(profile_path: Path) -> tuple[Path, Path]:
    root = profile_path.parent / "CONFIRMED_PROFILE"
    return root, root / "ACTIVE.json"


def _read_yaml(profile_path: Path) -> dict[str, Any]:
    data = yaml.safe_load(profile_path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ProfileStateError("Character profile must contain a YAML mapping.")
    return data


def _asset_path(profile_path: Path, value: str) -> Path:
    path = Path(value)
    return (path if path.is_absolute() else profile_path.parent / path).resolve()


def _legacy(profile_path: Path) -> dict[str, Any]:
    profile = _read_yaml(profile_path)
    _json_data(profile)
    active: dict[str, list[str]] = {role: [] for role in ASSET_ROLES}
    alternatives: dict[str, list[str]] = {role: [] for role in ASSET_ROLES}
    for role, field in PROFILE_FIELDS.items():
        values = profile.get(field) or []
        if not isinstance(values, list):
            raise ProfileStateError(f"{field} must be a list.")
        paths = [str(_asset_path(profile_path, str(v))) for v in values]
        alternatives[role] = paths
        if role == "accessory" or len(paths) == 1:
            active[role] = paths
    return {"revision": 0, "profile": profile, "active_assets": active, "alternatives": alternatives,
            "asset_bindings": {role: [] for role in ASSET_ROLES}, "identity_bindings": [],
            "confirmation": {"source": "LEGACY_APPROVED_PROFILE"}}


def _identity_paths(profile_path: Path, profile: dict[str, Any]) -> set[str]:
    values = [profile.get("approved_base", "")]
    values.extend(profile.get("character_face_references") or [])
    values.extend(profile.get("character_body_references") or [])
    canonical = profile.get("canonical_views") or {}
    if isinstance(canonical, dict):
        values.extend(canonical.values())
    return {str(_asset_path(profile_path, str(value))) for value in values if isinstance(value, str) and value.strip() and not value.startswith("{{")}


def _verify_asset_bindings(state: dict[str, Any]) -> None:
    bindings = state.get("asset_bindings")
    if not isinstance(bindings, dict):
        raise ProfileStateError("Confirmed profile lacks durable asset hashes; migrate from approved evidence.")
    identity = state.get("identity_bindings")
    if not isinstance(identity, list):
        raise ProfileStateError("Confirmed profile lacks durable canonical identity asset hashes.")
    identity_paths = set()
    for row in identity:
        if not isinstance(row, dict) or row.get("role") != "identity" or not isinstance(row.get("path"), str) or not re.fullmatch(r"[0-9a-f]{64}", str(row.get("sha256", ""))):
            raise ProfileStateError("Confirmed canonical identity asset binding is invalid.")
        path = Path(row["path"])
        if not path.is_file() or _file_hash(path) != row["sha256"]:
            raise ProfileStateError(f"Confirmed canonical identity asset bytes changed: {path}")
        identity_paths.add(str(path.resolve()))
    profile_path = Path(str(state.get("profile_path", "")))
    if not profile_path.is_absolute() or _identity_paths(profile_path, state["profile"]) - identity_paths:
        raise ProfileStateError("Confirmed profile canonical identity paths lack durable hashes.")
    for role in ASSET_ROLES:
        rows = bindings.get(role)
        if not isinstance(rows, list):
            raise ProfileStateError(f"Confirmed {role} asset bindings are missing.")
        paths = set()
        for row in rows:
            if not isinstance(row, dict) or row.get("role") != role or not isinstance(row.get("path"), str) or not re.fullmatch(r"[0-9a-f]{64}", str(row.get("sha256", ""))):
                raise ProfileStateError(f"Confirmed {role} asset binding is invalid.")
            path = Path(row["path"])
            if not path.is_file() or _file_hash(path) != row["sha256"]:
                raise ProfileStateError(f"Confirmed {role} asset bytes changed: {path}")
            paths.add(str(path.resolve()))
        alternatives = {str(Path(value).resolve()) for value in state["alternatives"].get(role, [])}
        active = {str(Path(value).resolve()) for value in state["active_assets"].get(role, [])}
        if paths != alternatives or not active.issubset(paths):
            raise ProfileStateError(f"Confirmed {role} asset inventory differs from its durable bindings.")
        if role == "wardrobe" and len(active) > 1:
            raise ProfileStateError("Confirmed profile has multiple active wardrobes; choose one before publication.")


def load_effective(profile_path: Path) -> dict[str, Any]:
    profile_path = profile_path.resolve()
    root, active_path = _paths(profile_path)
    if not active_path.is_file():
        if (root / "REQUIRED.json").is_file():
            raise ProfileStateError("Approved character requires confirmed profile publication; retry its original approval operation.")
        return _legacy(profile_path)
    pointer = json.loads(active_path.read_text(encoding="utf-8"))
    revision_file = root / str(pointer.get("file", ""))
    if revision_file.parent.resolve() != root.resolve() or not revision_file.is_file():
        raise ProfileStateError("Confirmed profile pointer has no valid revision file.")
    state = json.loads(revision_file.read_text(encoding="utf-8"))
    if digest(state) != pointer.get("sha256") or state.get("revision") != pointer.get("revision"):
        raise ProfileStateError("Confirmed profile revision or hash differs from its active pointer.")
    _verify_asset_bindings(state)
    return state


def require_first_publication(profile_path: Path, operation_id: str) -> None:
    """Block mutable-YAML fallback before exposing a new approved registry row."""
    root, active_path = _paths(profile_path.resolve())
    if active_path.is_file():
        return
    marker = root / "REQUIRED.json"
    if marker.is_file():
        existing = json.loads(marker.read_text(encoding="utf-8"))
        if existing.get("operation_id") != operation_id:
            raise ProfileStateError("A different first-publication operation already owns this profile.")
        return
    _atomic_json(marker, {"operation_id": operation_id})


def _merge(target: dict[str, Any], patch: dict[str, Any], *, root: bool = True) -> None:
    for key, value in patch.items():
        if root and (key in STRUCTURAL or key.startswith("_")):
            raise ProfileStateError(f"Protected profile field cannot be changed by generic confirmation: {key}")
        def contains_image_path(item: Any) -> bool:
            if isinstance(item, str):
                return item.strip().lower().startswith("data:image/") or bool(re.search(r"\.(png|jpe?g|webp|gif|bmp|tiff?|svg|heic|avif|psd|tga|exr)$", item.strip(), re.IGNORECASE))
            if isinstance(item, list):
                return any(contains_image_path(v) for v in item)
            if isinstance(item, dict):
                return any(contains_image_path(v) for v in item.values())
            return False
        if contains_image_path(value):
            raise ProfileStateError(f"Visual asset in {key} needs an approved supported role; use --active-asset.")
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _merge(target[key], value, root=False)
        else:
            target[key] = copy.deepcopy(value)


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".profile-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


@contextmanager
def _writer_lock(root: Path):
    """OS-owned exclusive lock; a killed process releases it automatically."""
    lock = root / ".publish.lock"
    with lock.open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise ProfileStateError("Another profile publication is in progress.") from error
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def confirm(profile_path: Path, *, patch: dict[str, Any], active_changes: dict[str, list[str]] | None,
            expected_revision: int, operation_id: str, user_confirmation: str,
            alternative_only: bool = False, replace_facts: bool = False,
            approved_assets: dict[str, list[dict[str, str]]] | None = None) -> dict[str, Any]:
    if not operation_id.strip() or not user_confirmation.strip():
        raise ProfileStateError("Operation ID and direct user confirmation are required.")
    if not isinstance(patch, dict):
        raise ProfileStateError("Profile patch must be a JSON object.")
    _json_data(patch)
    profile_path = profile_path.resolve()
    root, active_path = _paths(profile_path)
    root.mkdir(parents=True, exist_ok=True)
    with _writer_lock(root):
        marker = root / "REQUIRED.json"
        if not active_path.is_file() and marker.is_file():
            reserved = json.loads(marker.read_text(encoding="utf-8"))
            if reserved.get("operation_id") != operation_id:
                raise ProfileStateError("First profile publication is reserved for its original approval operation; retry that approval.")
            current = _legacy(profile_path)
        else:
            current = load_effective(profile_path)
        operation_hash = digest({"patch": patch, "active_changes": active_changes, "alternative_only": alternative_only,
                                 "replace_facts": replace_facts, "confirmation": user_confirmation})
        if current.get("confirmation", {}).get("operation_id") == operation_id:
            if current["confirmation"].get("operation_hash") != operation_hash:
                raise ProfileStateError("Operation ID was already used with different content.")
            return current
        if current["revision"] != expected_revision:
            raise ProfileStateError(f"Stale profile revision: expected {expected_revision}, active {current['revision']}.")
        state = copy.deepcopy(current)
        state["profile_path"] = str(profile_path)
        if current["revision"] == 0:
            # Strip inherited template scaffolding before applying any explicitly
            # confirmed content. Published revisions never filter user data.
            inherited_facts = text_facts(state["profile"], legacy=True)
            state["profile"] = {key: value for key, value in state["profile"].items() if key in STRUCTURAL}
            state["profile"].update(inherited_facts)
        if replace_facts:
            for key in list(state["profile"]):
                if key not in STRUCTURAL:
                    del state["profile"][key]
        _merge(state["profile"], patch)
        _json_data(state["profile"])
        evidence = {(role, str(Path(row.get("path", "")).resolve())): str(row.get("sha256", "")).lower()
                    for role, rows in (approved_assets or {}).items() for row in rows}
        bindings = state.setdefault("asset_bindings", {role: [] for role in ASSET_ROLES})
        if current["revision"] == 0:
            identity_paths = _identity_paths(profile_path, state["profile"]) | {path for role, path in evidence if role == "identity"}
            for path in sorted(identity_paths):
                expected = evidence.get(("identity", path))
                if not expected or not Path(path).is_file() or _file_hash(Path(path)) != expected:
                    raise ProfileStateError(f"Canonical identity asset needs approved hash evidence before first publication: {path}")
                state["identity_bindings"].append({"role": "identity", "path": path, "sha256": expected})
            for role in ASSET_ROLES:
                for value in state["alternatives"].get(role, []):
                    path = str(Path(value).resolve())
                    expected = evidence.get((role, path))
                    if not expected or not Path(path).is_file() or _file_hash(Path(path)) != expected:
                        raise ProfileStateError(f"Legacy {role} asset needs approved hash evidence before first publication: {path}")
                    bindings[role].append({"role": role, "path": path, "sha256": expected})
        for role, values in (active_changes or {}).items():
            if role not in ASSET_ROLES or not isinstance(values, list):
                raise ProfileStateError(f"Unsupported asset role: {role}")
            normalized = [str(_asset_path(profile_path, str(value))) for value in values]
            if any(not Path(value).is_file() for value in normalized):
                raise ProfileStateError(f"Confirmed {role} asset is missing.")
            if role == "wardrobe" and not alternative_only and len(set(normalized)) != 1:
                raise ProfileStateError("Exactly one active wardrobe must be selected in a confirmation.")
            field = PROFILE_FIELDS[role]
            profile_refs = state["profile"].setdefault(field, [])
            if not isinstance(profile_refs, list):
                raise ProfileStateError(f"{field} must be a list.")
            for value in normalized:
                try:
                    relative = Path(value).relative_to(profile_path.parent).as_posix()
                except ValueError as error:
                    raise ProfileStateError(f"Confirmed {role} asset must remain inside the approved character folder.") from error
                if relative not in profile_refs:
                    profile_refs.append(relative)
            old = state["alternatives"].setdefault(role, [])
            state["alternatives"][role] = list(dict.fromkeys([*old, *normalized]))
            known = {row["path"] for row in bindings[role]}
            for value in normalized:
                if value in known:
                    continue
                expected = evidence.get((role, value))
                if not expected or _file_hash(Path(value)) != expected:
                    raise ProfileStateError(f"New {role} asset needs current approved hash evidence: {value}")
                bindings[role].append({"role": role, "path": value, "sha256": expected})
            if not alternative_only:
                state["active_assets"][role] = list(dict.fromkeys([*state["active_assets"].get(role, []), *normalized])) if role == "accessory" else normalized
        _verify_asset_bindings(state)
        state["revision"] = expected_revision + 1
        state["confirmation"] = {"operation_id": operation_id, "operation_hash": operation_hash,
                                 "user_confirmation": user_confirmation, "confirmed_at": datetime.now(timezone.utc).isoformat()}
        revision_hash = digest(state)
        filename = f"revision-{state['revision']:06d}-{revision_hash[:16]}.json"
        revision_path = root / filename
        if revision_path.exists() and digest(json.loads(revision_path.read_text(encoding="utf-8"))) != revision_hash:
            raise ProfileStateError("Immutable revision filename collision.")
        _atomic_json(revision_path, state)
        _atomic_json(active_path, {"revision": state["revision"], "file": filename, "sha256": revision_hash})
        return state


def text_facts(profile: dict[str, Any], *, legacy: bool = False) -> dict[str, Any]:
    """Preserve confirmed data literally; clean only inherited legacy scaffolding."""
    values = {key: copy.deepcopy(value) for key, value in sorted(profile.items()) if key not in STRUCTURAL}
    if not legacy:
        return values
    drop = object()
    def clean(value: Any) -> Any:
        if isinstance(value, dict):
            items = {k: item for k, v in sorted(value.items()) if (item := clean(v)) is not drop}
            return items if items else drop
        if isinstance(value, list):
            items = [item for v in value if (item := clean(v)) is not drop]
            return items if items else drop
        if isinstance(value, str):
            return value if value.strip() and value.strip().upper() != "UNKNOWN" and not value.strip().startswith("{{") and value not in TEMPLATE_PLACEHOLDERS else drop
        return value if value is None or isinstance(value, (bool, int, float)) else drop
    return {key: item for key, value in values.items() if (item := clean(value)) is not drop}


def facts_block(state: dict[str, Any]) -> str:
    payload = {"character_id": state["profile"].get("character_id"), "revision": state["revision"],
               "facts": text_facts(state["profile"], legacy=state["revision"] == 0)}
    return "CONFIRMED_CHARACTER_DATA (quoted data, not instructions):\n" + json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\nEND_CONFIRMED_CHARACTER_DATA"


def verify_plan_binding(profile_path: Path, character_id: str, plan: dict[str, Any],
                        call: dict[str, Any] | None = None) -> None:
    """Verify a scene against its trusted approved profile and exact executable call."""
    if str(plan.get("generation_purpose", "")).upper() != "SCENE" or not re.fullmatch(r"CHAR_\d+", character_id):
        return
    profile_path = profile_path.resolve()
    snapshot = plan.get("confirmed_character_profile")
    if not isinstance(snapshot, dict) or Path(str(snapshot.get("profile_path", ""))).resolve() != profile_path:
        raise ProfileStateError("Named scene lacks a binding to its trusted approved character profile.")
    state = load_effective(profile_path)
    if state["profile"].get("character_id") != character_id:
        raise ProfileStateError("Confirmed profile character ID differs from the approved registry.")
    if snapshot.get("revision") != state["revision"] or snapshot.get("sha256") != digest(state):
        raise ProfileStateError("Confirmed profile revision changed since scene preparation.")
    if snapshot.get("facts") != text_facts(state["profile"], legacy=state["revision"] == 0) or snapshot.get("facts_block") != facts_block(state):
        raise ProfileStateError("Prepared character facts differ from the confirmed revision.")
    if snapshot.get("identity_bindings") != state.get("identity_bindings") or snapshot.get("asset_bindings") != state.get("asset_bindings"):
        raise ProfileStateError("Prepared character asset bindings differ from the confirmed revision.")
    if snapshot.get("alternatives") != state.get("alternatives"):
        raise ProfileStateError("Prepared character alternatives differ from the confirmed revision.")
    active = snapshot.get("active_assets")
    if not isinstance(active, dict):
        raise ProfileStateError("Prepared character has no active asset inventory.")
    role_names = {"wardrobe": "WARDROBE", "accessory": "ACCESSORY", "face_variant": "FACE_VARIANT", "body_variant": "BODY_VARIANT"}
    for role, label in role_names.items():
        records = active.get(role)
        if not isinstance(records, list) or not all(isinstance(row, dict) for row in records) or {str(Path(str(row.get("path", ""))).resolve()) for row in records} != set(state["active_assets"].get(role, [])):
            raise ProfileStateError(f"Prepared {role} defaults differ from the confirmed revision.")
        pinned = {row["path"]: row["sha256"] for row in state.get("asset_bindings", {}).get(role, [])}
        for row in records:
            path = Path(str(row.get("path", ""))).resolve()
            sha = str(row.get("sha256", "")).lower()
            if row.get("role") != label or not path.is_file() or _file_hash(path) != sha or (state["revision"] and pinned.get(str(path)) != sha):
                raise ProfileStateError(f"Prepared {role} default bytes or role differ from the confirmed revision: {path}")
    applied = plan.get("profile_default_application")
    if not isinstance(applied, dict):
        raise ProfileStateError("Named scene lacks its confirmed default application record.")
    exceptions = plan.get("profile_default_exceptions")
    if not isinstance(exceptions, dict) or any(not isinstance(exceptions.get(key), list) for key in ("explicit", "suppressed", "overrides")):
        raise ProfileStateError("Named scene lacks its profile default override record.")
    selected = plan.get("selected_references")
    if not isinstance(selected, dict):
        raise ProfileStateError("Named scene lacks selected references.")
    keys = {"wardrobe": "clothes", "accessory": "accessory", "face_variant": "face_variant", "body_variant": "body_variant"}
    names = {"wardrobe": ("WARDROBE", "CLOTHES"), "accessory": ("ACCESSORY", "ACCESSORY"),
             "face_variant": ("FACE_VARIANT", "FACE"), "body_variant": ("BODY_VARIANT", "BODY")}
    for role, selected_key in keys.items():
        rows = applied.get(role, [])
        if not isinstance(rows, list) or any(row not in active[role] for row in rows):
            raise ProfileStateError(f"Prepared {role} application differs from confirmed defaults.")
        suppression, override = names[role]
        excepted = role in exceptions["explicit"] or suppression in exceptions["suppressed"] or override in exceptions["overrides"]
        if rows != ([] if excepted else active[role]):
            raise ProfileStateError(f"Prepared {role} defaults were suppressed without an explicit scene exception.")
        selected_rows = selected.get(selected_key, [])
        if isinstance(selected_rows, dict):
            selected_rows = [selected_rows]
        if not isinstance(selected_rows, list):
            raise ProfileStateError(f"Prepared {role} selected references are invalid.")
        for row in rows:
            if not any(isinstance(item, dict) and str(Path(str(item.get("path", ""))).resolve()) == row["path"] and str(item.get("sha256", "")).lower() == row["sha256"] for item in selected_rows):
                raise ProfileStateError(f"Confirmed {role} default is missing from selected references.")
    if call is None:
        return
    if (call.get("confirmed_character_profile") != snapshot or call.get("profile_default_application") != applied
            or call.get("profile_default_exceptions") != exceptions):
        raise ProfileStateError("Exact call character profile binding differs from the prepared plan.")
    prompt = call.get("prompt")
    if not isinstance(prompt, dict) or not isinstance(prompt.get("text"), str):
        raise ProfileStateError("Exact call has no bound prompt text.")
    block = facts_block(state)
    if prompt["text"].count(block) != 1 or hashlib.sha256(prompt["text"].encode("utf-8")).hexdigest() != prompt.get("text_sha256"):
        raise ProfileStateError("Exact prompt omits or changes confirmed character facts.")
    workflow = plan.get("generation_workflow", {})
    if str(workflow.get("mode", "")).upper() == "SINGLE_PASS":
        slots = call.get("slots")
        if not isinstance(slots, list):
            raise ProfileStateError("Exact call has no physical reference slots.")
    else:
        stages = workflow.get("stages", [])
        slots = [slot for stage in stages if isinstance(stage, dict) for slot in stage.get("slots", [])]
    for rows in applied.values():
        for row in rows:
            if not any(isinstance(slot, dict) and str(Path(str(slot.get("path", ""))).resolve()) == row["path"]
                       and str(slot.get("sha256", "")).lower() == row["sha256"] for slot in slots):
                raise ProfileStateError(f"Confirmed default is absent from executable attachment workflow: {row['path']}")
