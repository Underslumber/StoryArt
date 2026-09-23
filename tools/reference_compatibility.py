"""Role-aware checks for transferring information from reference images.

Compatibility metadata is explicit and review-backed. Missing or legacy values
stay UNKNOWN; this module never derives anatomy from a filename or a person's
appearance.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable


UNKNOWN = "UNKNOWN"
ANATOMY_ROLES = {"BODY", "ANATOMY", "BODY_BUILD", "BODY_BUILD_TARGET", "PHYSIOLOGY", "AUX_BODY_BUILD", "CHARACTER_BODY"}
SOFT_ROLES = {
    "POSE", "POSE_SOFT", "AUX_POSE", "STYLE", "STYLE_SOFT", "BACKGROUND", "BACKGROUND_STYLE",
    "CAMERA", "CAMERA_ONLY", "AUX_CAMERA", "CLOTHES", "CLOTHING_BEHAVIOR",
    "AUX_CLOTHING_BEHAVIOR", "OBJECT_INTERACTION", "AUX_OBJECT_INTERACTION", "AUX_CONTACT_POINTS",
    "LIGHTING", "COMPOSITION",
}
ANATOMY_VALUES = {"MALE_ANATOMY", "FEMALE_ANATOMY", "ANDROGYNOUS_ANATOMY"}


def _is_full_body_reference(reference: dict[str, Any]) -> bool:
    for key in ("coverage", "source_coverage", "framing", "shot_type"):
        value = _clean(reference.get(key)).upper().replace("-", "_").replace(" ", "_")
        if value in {"FULL", "FULL_BODY", "FULLBODY", "FULL_LENGTH", "FULL_LENGTH_BODY"}:
            return True
    return False


def _clean(value: Any) -> str:
    text = str(value or "").strip()
    return text if text else UNKNOWN


def _values(value: Any) -> set[str]:
    if isinstance(value, (list, tuple, set)):
        parts = value
    else:
        parts = re.split(r"[;,|]", _clean(value))
    result = {str(part).strip().upper() for part in parts if str(part).strip()}
    result.discard(UNKNOWN)
    return result


def _yaml_scalar(text: str, key: str, section: str | None = None) -> str:
    """Read one scalar from the project's simple canonical YAML safely.

    This intentionally supports only unquoted/quoted scalar values and the
    known identity sections; unsupported YAML yields UNKNOWN rather than a guess.
    """
    lines = text.splitlines()
    in_section = section is None
    section_indent = 0
    matches: list[str] = []
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if section is None and indent != 0:
            continue
        if section is not None and indent == 0 and line.rstrip().endswith(":"):
            in_section = line.strip() == f"{section}:"
            section_indent = indent
            continue
        if not in_section or (section is not None and indent <= section_indent):
            continue
        match = re.match(rf"\s*{re.escape(key)}\s*:\s*(.*?)\s*$", line)
        if not match:
            continue
        value = match.group(1).split(" #", 1)[0].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if value.startswith("{{") or value in {"", "null", "~"}:
            return UNKNOWN
        matches.append(value.upper())
    return matches[0] if len(matches) == 1 else UNKNOWN


def load_character_identity(profile_path: str | Path) -> dict[str, str]:
    """Load canonical identity metadata, defaulting every absent fact to UNKNOWN."""
    path = Path(profile_path)
    result = {
        "character_id": UNKNOWN,
        "character_name": UNKNOWN,
        "profile_status": UNKNOWN,
        "gender_identity": UNKNOWN,
        "visible_presentation": UNKNOWN,
        "target_anatomy": UNKNOWN,
        "anatomy_evidence_source": UNKNOWN,
        "status": "UNKNOWN",
        "profile_path": str(path),
    }
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        return result
    if path.suffix.lower() == ".json":
        try:
            data = json.loads(text)
            identity = data.get("identity", {}) if isinstance(data, dict) else {}
            anatomy = data.get("anatomy_compatibility", {}) if isinstance(data, dict) else {}
            result.update({
                "character_id": _clean(data.get("character_id")) if isinstance(data, dict) else UNKNOWN,
                "character_name": _clean(data.get("name")) if isinstance(data, dict) else UNKNOWN,
                "profile_status": _clean(data.get("status")) if isinstance(data, dict) else UNKNOWN,
                "gender_identity": _clean(identity.get("gender_identity")) if isinstance(identity, dict) else UNKNOWN,
                "visible_presentation": _clean(identity.get("visible_presentation")) if isinstance(identity, dict) else UNKNOWN,
                "target_anatomy": _clean(anatomy.get("target_anatomy")) if isinstance(anatomy, dict) else UNKNOWN,
                "anatomy_evidence_source": _clean(anatomy.get("evidence_source")) if isinstance(anatomy, dict) else UNKNOWN,
            })
        except (ValueError, TypeError):
            return result
    else:
        result.update({
            "character_id": _yaml_scalar(text, "character_id"),
            "character_name": _yaml_scalar(text, "name"),
            "profile_status": _yaml_scalar(text, "status"),
            "gender_identity": _yaml_scalar(text, "gender_identity", "identity"),
            "visible_presentation": _yaml_scalar(text, "visible_presentation", "identity"),
            "target_anatomy": _yaml_scalar(text, "target_anatomy", "anatomy_compatibility"),
            "anatomy_evidence_source": _yaml_scalar(text, "evidence_source", "anatomy_compatibility"),
        })
    result["status"] = "KNOWN" if result["character_id"] != UNKNOWN else "UNKNOWN"
    return result


def validate_reference_compatibility(
    target: dict[str, Any] | str | Path,
    reference: dict[str, Any],
    roles: Iterable[str] | str,
) -> dict[str, Any]:
    """Return an auditable compatibility decision for selected reference roles."""
    identity = load_character_identity(target) if isinstance(target, (str, Path)) else dict(target)
    target_anatomy = _clean(identity.get("target_anatomy"))
    target_id = _clean(identity.get("character_id"))
    if isinstance(roles, str):
        role_set = {part.strip().upper() for part in re.split(r"[;,]", roles) if part.strip()}
    else:
        role_set = {str(part).strip().upper() for part in roles if str(part).strip()}
    ref_character = _clean(reference.get("source_character_id"))
    ref_compat = _values(reference.get("subject_anatomy_compatibility")) | _values(reference.get("anatomy_compatibility"))
    evidence_values = [
        _clean(reference.get(key))
        for key in ("subject_anatomy_evidence_source", "anatomy_evidence_source", "evidence_source")
        if _clean(reference.get(key)) != UNKNOWN
    ]
    evidence = evidence_values[0] if evidence_values else UNKNOWN
    source_character = _clean(reference.get("source_character_id"))
    forbidden: list[str] = []
    decisions: list[tuple[str, str]] = []
    if not role_set:
        decisions.append(("REVIEW_REQUIRED", "Role is missing or has no compatibility policy."))
    for role in sorted(role_set):
        if role in ANATOMY_ROLES:
            if target_id != UNKNOWN and source_character == target_id:
                decisions.append(("ALLOWED", "Reference is the same canonical character's approved body source."))
            elif target_anatomy == UNKNOWN:
                decisions.append(("REVIEW_REQUIRED", "Target anatomy metadata is UNKNOWN."))
            elif _clean(identity.get("anatomy_evidence_source")) == UNKNOWN:
                decisions.append(("REVIEW_REQUIRED", "Target anatomy has no explicit review-backed evidence source."))
            elif evidence != UNKNOWN and target_anatomy in {"MALE_ANATOMY", "FEMALE_ANATOMY", "ANDROGYNOUS_ANATOMY"} and ref_compat & {"MALE_ANATOMY", "FEMALE_ANATOMY", "ANDROGYNOUS_ANATOMY"} and target_anatomy not in ref_compat:
                decisions.append(("BLOCKED", "Reviewed anatomy compatibility excludes the target anatomy."))
            elif evidence == UNKNOWN or target_anatomy not in ref_compat:
                decisions.append(("REVIEW_REQUIRED", "Reference has no reviewed compatibility evidence for target anatomy."))
            else:
                decisions.append(("ALLOWED", "Reviewed anatomy compatibility explicitly matches the target."))
        elif role == "FACE":
            if target_id == UNKNOWN or ref_character == UNKNOWN:
                decisions.append(("REVIEW_REQUIRED", "Canonical character identity is missing."))
            elif target_id != ref_character:
                decisions.append(("BLOCKED", "Face identity references must belong to the canonical target character."))
            else:
                decisions.append(("ALLOWED", "Canonical character identity matches."))
        elif role in SOFT_ROLES:
            anatomy_visible = role == "AUX_OBJECT_INTERACTION" or (
                _is_full_body_reference(reference) and role not in {"POSE", "POSE_SOFT", "AUX_POSE"}
            )
            if anatomy_visible:
                if target_anatomy not in ANATOMY_VALUES:
                    decisions.append(("REVIEW_REQUIRED", "Target anatomy is UNKNOWN for an anatomy-visible body reference."))
                elif _clean(identity.get("anatomy_evidence_source")) == UNKNOWN:
                    decisions.append(("REVIEW_REQUIRED", "Target anatomy has no explicit review-backed evidence source."))
                elif evidence == UNKNOWN or not ref_compat:
                    decisions.append(("REVIEW_REQUIRED", "Visible full-body reference has no reviewed subject-anatomy compatibility evidence."))
                elif target_anatomy not in ref_compat:
                    decisions.append(("BLOCKED", "Reviewed subject-anatomy compatibility excludes the target anatomy."))
                else:
                    decisions.append(("ALLOWED", "Reviewed subject-anatomy compatibility explicitly matches the target."))
                forbidden = ["Do not transfer gender identity, visible presentation, or anatomy beyond the reviewed interaction geometry."]
                continue
            decisions.append(("ALLOWED", "Soft reference role can cross visible presentation."))
            forbidden = ["Do not transfer gender identity, visible presentation, or anatomy."]
        else:
            decisions.append(("REVIEW_REQUIRED", f"Role has no compatibility policy: {role}."))
    precedence = {"ALLOWED": 0, "REVIEW_REQUIRED": 1, "BLOCKED": 2}
    status = max((decision[0] for decision in decisions), key=precedence.__getitem__)
    reason = "; ".join(dict.fromkeys(decision[1] for decision in decisions))
    return {
        "compatible": status == "ALLOWED",
        "status": status,
        "reason": reason,
        "roles": sorted(role_set),
        "target_anatomy": target_anatomy,
        "reference_anatomy_compatibility": sorted(ref_compat) or [UNKNOWN],
        "evidence_source": evidence,
        "forbidden_transfers": forbidden,
    }
