#!/usr/bin/env python3
"""Estimate image-generation rejection risk without attempting policy evasion."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path


WORKSPACE = Path(__file__).resolve().parents[1]
DEFAULT_LEXICON = WORKSPACE / "config" / "generation_risk_lexicon.json"
RISK_RE = re.compile(r"^D([1-9]|10)$", re.IGNORECASE)
REFERENCE_SPEC_SEPARATOR = "::"
CANONICAL_ROLES = {
    "STYLE", "SUBJECT", "PRIMARY_FACE", "SUPPORTING_FACE", "EXPRESSION", "CHARACTER_ASSEMBLY",
    "BODY", "POSE", "CLOTHES", "LIGHTING", "BACKGROUND", "COMPOSITION", "COVERAGE_FRONT",
    "COVERAGE_SIDE", "COVERAGE_BACK", "STYLE_STAGE", "FACE_IDENTITY_STAGE", "PHYSIQUE_FRONT_STAGE",
    "PHYSIQUE_SIDE_STAGE", "PHYSIQUE_BACK_STAGE", "FACE_FRONT_TARGETED_PACK", "FACE_FRONT_SIDE_TARGETED_PACK",
    "BODY_POSE_STAGE", "CLOTHING_STAGE", "CHARACTER_COMPOSITE_STAGE", "CLOTHING_TOPOLOGY", "FACE",
    "CHARACTER_FACE", "CHARACTER_BODY", "ANATOMY", "POSE_SOFT", "CAMERA_ONLY", "MULTIVIEW_BODY",
}
BR_ROLE_SUFFIXES = {
    "AUX_BODY_BUILD", "AUX_POSE", "AUX_CAMERA", "AUX_CLOTHING_BEHAVIOR", "AUX_OBJECT_INTERACTION",
    "AUX_CONTACT_POINTS", "STAGING_ONLY", "BODY_BUILD_TARGET", "CLOTHING_BEHAVIOR", "OBJECT_INTERACTION",
    "CAMERA_ONLY",
}


class RiskAssessmentError(RuntimeError):
    pass


def _normalise_roles(value: object) -> list[str]:
    if isinstance(value, str):
        values = re.split(r"[,;]", value)
    elif isinstance(value, (list, tuple, set)):
        values = list(value)
    else:
        values = []
    return sorted({str(role).strip().upper() for role in values if str(role).strip()})


def _reference_bindings(references: list[dict[str, object]]) -> list[dict[str, object]]:
    """Hash selected files and union roles for duplicate physical content."""
    by_hash: dict[str, dict[str, object]] = {}
    for reference in references:
        path = Path(str(reference.get("path", ""))).expanduser().resolve()
        if not path.is_file():
            raise RiskAssessmentError(f"Reference does not exist: {path}")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        declared = str(reference.get("sha256", "")).strip().lower()
        if declared and declared != digest:
            raise RiskAssessmentError(f"Selected reference hash does not match file: {path}")
        roles = _normalise_roles(reference.get("active_roles", reference.get("role", [])))
        if not roles or any(
            role in {"UNKNOWN", "UNSPECIFIED", "NONE"}
            or (role not in CANONICAL_ROLES and not re.fullmatch(
                r"BR_\d+:(?:" + "|".join(sorted(BR_ROLE_SUFFIXES)) + r")", role
            ))
            for role in roles
        ):
            raise RiskAssessmentError(f"Every selected reference needs explicit active roles: {path}")
        binding = by_hash.setdefault(digest, {"sha256": digest, "active_roles": set()})
        binding["active_roles"].update(roles)
    return [
        {"sha256": digest, "active_roles": sorted(binding["active_roles"])}
        for digest, binding in sorted(by_hash.items())
    ]


def validate_assessment(
    report: dict[str, object], prompt_text: str, references: list[dict[str, object]],
    lexicon_path: Path = DEFAULT_LEXICON,
) -> None:
    """Validate a new strict report against the exact prompt and physical inputs.

    Reference visual ratings remain explicit human judgments; this function
    verifies their provenance and arithmetic, not the visual judgment itself.
    """
    binding = report.get("input_binding")
    if not isinstance(binding, dict):
        raise RiskAssessmentError("Legacy risk report is readable but is not bound to exact inputs.")
    if binding.get("prompt_sha256") != sha256_text(prompt_text):
        raise RiskAssessmentError("Risk assessment prompt hash does not match the exact requested prompt.")
    if binding.get("lexicon_sha256") != hashlib.sha256(lexicon_path.read_bytes()).hexdigest():
        raise RiskAssessmentError("Risk assessment lexicon differs from the currently selected lexicon.")
    expected_bindings = _reference_bindings(references)
    actual_bindings = binding.get("references")
    if not isinstance(actual_bindings, list):
        raise RiskAssessmentError("Risk assessment has no explicit reference binding.")
    actual: dict[str, set[str]] = {}
    for row in actual_bindings:
        if not isinstance(row, dict):
            raise RiskAssessmentError("Invalid reference binding row.")
        digest = str(row.get("sha256", "")).lower()
        roles = set(_normalise_roles(row.get("active_roles", [])))
        if not re.fullmatch(r"[0-9a-f]{64}", digest) or not roles:
            raise RiskAssessmentError("Reference binding needs a valid hash and explicit active roles.")
        if digest in actual:
            raise RiskAssessmentError("Duplicate physical reference hash in risk report.")
        actual[digest] = roles
    wanted = {str(row["sha256"]): set(row["active_roles"]) for row in expected_bindings}
    if actual != wanted:
        raise RiskAssessmentError("Risk assessment reference files or active roles do not match the request.")

    report_references = report.get("references")
    if not isinstance(report_references, list) or len(report_references) != len(expected_bindings):
        raise RiskAssessmentError("Risk report must assess each unique physical reference exactly once.")
    rows_by_hash: dict[str, dict[str, object]] = {}
    for row in report_references:
        if not isinstance(row, dict):
            raise RiskAssessmentError("Invalid assessed reference row.")
        digest = str(row.get("sha256", "")).lower()
        if digest in rows_by_hash:
            raise RiskAssessmentError("Duplicate physical reference assessment.")
        if not str(row.get("reason_ru", "")).strip():
            raise RiskAssessmentError("Human reference assessment needs a reason.")
        if set(_normalise_roles(row.get("active_roles", []))) != actual.get(digest):
            raise RiskAssessmentError("Assessed reference roles do not match the bound active roles.")
        rows_by_hash[digest] = row
    for row in expected_bindings:
        assessed = rows_by_hash.get(str(row["sha256"]))
        if assessed is None:
            raise RiskAssessmentError("A selected physical reference has no human risk assessment.")
        content = parse_d(str(assessed.get("content_and_reference_risk", "")))
        impact_text = str(assessed.get("use_impact", ""))
        impact_match = re.fullmatch(r"([+-]?)([0-2])D", impact_text, flags=re.IGNORECASE)
        if not impact_match:
            raise RiskAssessmentError("Reference assessment has invalid use impact.")
        impact = int(impact_match.group(2)) * (-1 if impact_match.group(1) == "-" else 1)
        if parse_d(str(assessed.get("effective_risk", ""))) != clamp(content + impact):
            raise RiskAssessmentError("Reference effective risk does not match its human rating and use impact.")

    original = report.get("original_prompt")
    if not isinstance(original, dict) or original.get("text_sha256") != sha256_text(str(original.get("text", ""))):
        raise RiskAssessmentError("Original prompt assessment is missing its exact text hash.")
    recomputed_original = evaluate_prompt(str(original["text"]), load_lexicon(lexicon_path))
    if original.get("score") != recomputed_original["score"] or original.get("risk") != recomputed_original["risk"]:
        raise RiskAssessmentError("Original prompt heuristic score does not match the current lexicon evaluation.")
    original_combined, _ = combined_score(int(recomputed_original["score"]), report_references)
    if report.get("original_combined_risk") != d_label(original_combined):
        raise RiskAssessmentError("Original combined risk does not match its prompt and references.")

    prompt = report.get("revised_prompt") or original
    if not isinstance(prompt, dict) or prompt.get("text") != prompt_text or prompt.get("text_sha256") != sha256_text(prompt_text):
        raise RiskAssessmentError("Assessed prompt payload does not match its input binding.")
    recomputed_prompt = evaluate_prompt(prompt_text, load_lexicon(lexicon_path))
    if prompt.get("score") != recomputed_prompt["score"] or prompt.get("risk") != recomputed_prompt["risk"]:
        raise RiskAssessmentError("Prompt heuristic score does not match the current lexicon evaluation.")
    if report.get("revised_prompt") is not None:
        revised_combined, _ = combined_score(int(recomputed_prompt["score"]), report_references)
        if report.get("revised_combined_risk") != d_label(revised_combined):
            raise RiskAssessmentError("Revised combined risk does not match its prompt and references.")

    recomputed_combined, _ = combined_score(int(recomputed_prompt["score"]), report_references)
    expected_label = d_label(recomputed_combined)
    if report.get("generation_risk") != expected_label:
        raise RiskAssessmentError("Combined generation risk does not match assessed prompt and references.")


def clamp(value: int) -> int:
    return max(1, min(10, value))


def d_label(value: int) -> str:
    return f"D{clamp(value)}"


def parse_d(value: str) -> int:
    match = RISK_RE.fullmatch(value.strip())
    if not match:
        raise RiskAssessmentError(f"Risk must be D1-D10, got: {value}")
    return int(match.group(1))


def load_lexicon(path: Path) -> dict[str, object]:
    if not path.is_file():
        raise RiskAssessmentError(f"Risk lexicon does not exist: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1:
        raise RiskAssessmentError("Unsupported risk lexicon schema.")
    return data


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def find_matches(text: str, records: list[dict[str, object]]) -> list[dict[str, object]]:
    matches: list[dict[str, object]] = []
    for record in records:
        found: list[str] = []
        for pattern in record.get("patterns", []):
            found.extend(match.group(0) for match in re.finditer(str(pattern), text, flags=re.IGNORECASE))
        if found:
            item = dict(record)
            item["matched_text"] = list(dict.fromkeys(found))
            matches.append(item)
    return matches


def evaluate_prompt(text: str, lexicon: dict[str, object]) -> dict[str, object]:
    elements = find_matches(text, list(lexicon.get("entries", [])))
    modifiers = find_matches(text, list(lexicon.get("modifiers", [])))
    review_flags = find_matches(text, list(lexicon.get("review_flags", [])))
    base = max((int(item["base_level"]) for item in elements), default=1)
    positive_impacts = sorted((int(item.get("impact", 0)) for item in elements), reverse=True)
    stacked_impact = min(2, sum(positive_impacts[1:])) if len(positive_impacts) > 1 else 0

    element_ids = {str(item["id"]) for item in elements}
    sexual_context_ids = {
        "explicit_sexual_activity", "exposed_intimate_anatomy", "sexualized_framing", "revealing_clothing",
    }
    severe_context_ids = sexual_context_ids | {"graphic_violence", "self_harm", "dehumanizing_hate"}

    def modifier_applies(item: dict[str, object]) -> bool:
        modifier_id = str(item["id"])
        if modifier_id in {"adult_unambiguous", "nonsexual_context", "opaque_coverage"}:
            if not element_ids.intersection(sexual_context_ids):
                return False
            if element_ids.intersection(
                {"explicit_sexual_activity", "exposed_intimate_anatomy"}
            ):
                return False
        if modifier_id == "photorealistic" and not element_ids.intersection(severe_context_ids | {"ambiguous_youth"}):
            return False
        if modifier_id == "intimate_camera" and not element_ids.intersection(sexual_context_ids):
            return False
        return True

    applied_modifier_ids = {str(item["id"]) for item in modifiers if modifier_applies(item)}
    modifier_delta = sum(
        int(item["delta"])
        for item in modifiers
        if str(item["id"]) in applied_modifier_ids
    )
    modifier_delta = max(-3, min(4, modifier_delta))
    score = clamp(base + stacked_impact + modifier_delta)

    age_sexualization = "ambiguous_youth" in element_ids and bool(
        element_ids & {"explicit_sexual_activity", "exposed_intimate_anatomy", "sexualized_framing", "revealing_clothing"}
    )
    hard_rules: list[str] = []
    if age_sexualization:
        score = 10
        hard_rules.append("Возрастная неоднозначность сочетается с сексуализацией: итог принудительно D10.")

    risk_elements = [
        {
            "id": item["id"],
            "label_ru": item["label_ru"],
            "matched_text": item["matched_text"],
            "base_level": d_label(int(item["base_level"])),
            "impact": f"+{int(item.get('impact', 0))}D",
            "context_ru": item["context_ru"],
            "safe_replacements_ru": item.get("safe_replacements_ru", []),
        }
        for item in elements
    ]
    applied_modifiers = [
        {
            "id": item["id"],
            "label_ru": item["label_ru"],
            "matched_text": item["matched_text"],
            "delta": f"{int(item['delta']):+d}D",
            "applied": str(item["id"]) in applied_modifier_ids,
            "condition_ru": item["condition_ru"],
        }
        for item in modifiers
    ]
    suggestions = [replacement for item in risk_elements for replacement in item["safe_replacements_ru"]]
    return {
        "text": text,
        "text_sha256": sha256_text(text),
        "risk": d_label(score),
        "score": score,
        "risk_elements": risk_elements,
        "modifiers": applied_modifiers,
        "hard_rules": hard_rules,
        "review_flags": [
            {"id": item["id"], "label_ru": item["label_ru"], "matched_text": item["matched_text"], "context_ru": item["context_ru"]}
            for item in review_flags
        ],
        "safer_formulation_suggestions_ru": list(dict.fromkeys(suggestions)),
    }


def parse_reference_spec(value: str) -> dict[str, object]:
    parts = value.split(REFERENCE_SPEC_SEPARATOR, 4)
    if len(parts) not in {4, 5}:
        raise RiskAssessmentError(
            "Reference must use PATH::D1-D10::-2D..+2D::REASON[::ROLE[,ROLE...]]. "
            "The rating must cover both image content and its likely influence when attached."
        )
    path_text, risk_text, impact_text, reason = (part.strip() for part in parts[:4])
    roles = _normalise_roles(parts[4] if len(parts) == 5 else [])
    path = Path(path_text).resolve()
    if not path.is_file():
        raise RiskAssessmentError(f"Reference does not exist: {path}")
    risk = parse_d(risk_text)
    impact_match = re.fullmatch(r"([+-]?)([0-2])D", impact_text, flags=re.IGNORECASE)
    if not impact_match:
        raise RiskAssessmentError(f"Reference use impact must be -2D..+2D, got: {impact_text}")
    sign = -1 if impact_match.group(1) == "-" else 1
    impact = sign * int(impact_match.group(2))
    if not reason:
        raise RiskAssessmentError(f"Reference risk reason is required: {path}")
    effective = clamp(risk + impact)
    return {
        "path": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "label": f"{path.name} [{d_label(risk)}]",
        "content_and_reference_risk": d_label(risk),
        "use_impact": f"{impact:+d}D",
        "effective_risk": d_label(effective),
        "reason_ru": reason,
        "active_roles": roles,
    }


def combined_score(prompt_score: int, references: list[dict[str, object]]) -> tuple[int, list[dict[str, str]]]:
    effective = [parse_d(str(item["effective_risk"])) for item in references]
    score = max([prompt_score, *effective], default=prompt_score)
    modifiers: list[dict[str, str]] = []
    if sum(value >= 6 for value in effective) >= 2:
        score = clamp(score + 1)
        modifiers.append({
            "id": "multiple_intimate_references",
            "delta": "+1D",
            "reason_ru": "Два или более референса с эффективным риском D6+ усиливают риск совместного запроса.",
        })
    return score, modifiers


def read_prompt(args: argparse.Namespace) -> str:
    if bool(args.text) == bool(args.prompt_file):
        raise RiskAssessmentError("Supply exactly one of --text or --prompt-file.")
    return args.text if args.text else Path(args.prompt_file).read_text(encoding="utf-8")


def command_assess(args: argparse.Namespace) -> None:
    lexicon = load_lexicon(args.lexicon)
    original_text = read_prompt(args)
    original = evaluate_prompt(original_text, lexicon)
    parsed_references = [parse_reference_spec(value) for value in args.reference]
    references_by_hash: dict[str, dict[str, object]] = {}
    for reference in parsed_references:
        digest = str(reference["sha256"])
        existing = references_by_hash.get(digest)
        if existing is None:
            references_by_hash[digest] = reference
            continue
        for field in ("content_and_reference_risk", "use_impact", "effective_risk", "reason_ru"):
            if existing[field] != reference[field]:
                raise RiskAssessmentError(f"Duplicate physical reference has conflicting assessments: {reference['path']}")
        existing["active_roles"] = sorted(set(existing["active_roles"]) | set(reference["active_roles"]))
    references = list(references_by_hash.values())
    original_combined, combined_modifiers = combined_score(int(original["score"]), references)
    revised = None
    revised_combined = None
    if args.revised_text or args.revised_prompt_file:
        if bool(args.revised_text) == bool(args.revised_prompt_file):
            raise RiskAssessmentError("Supply exactly one revised prompt source.")
        revised_text = args.revised_text if args.revised_text else Path(args.revised_prompt_file).read_text(encoding="utf-8")
        revised = evaluate_prompt(revised_text, lexicon)
        revised_combined, _ = combined_score(int(revised["score"]), references)

    report = {
        "schema_version": 1,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "notice_ru": lexicon["notice_ru"],
        "calculation_ru": "Максимальный базовый риск + контекстные сочетания + применимые модификаторы; затем максимум с эффективным риском референсов и +1D при двух и более D6+ референсах; итог ограничен D1-D10.",
        "original_prompt": original,
        "references": references,
        "combined_modifiers": combined_modifiers,
        "original_combined_risk": d_label(original_combined),
        "revised_prompt": revised,
        "revised_combined_risk": d_label(revised_combined) if revised_combined is not None else None,
        "generation_risk": d_label(revised_combined if revised_combined is not None else original_combined),
        "input_binding": {
            "lexicon_sha256": hashlib.sha256(Path(args.lexicon).read_bytes()).hexdigest(),
            "prompt_sha256": sha256_text(
                str(revised["text"]) if revised is not None else str(original["text"])
            ),
            "references": [
                {"sha256": item["sha256"], "active_roles": item["active_roles"]}
                for item in references
            ],
        },
    }
    serialized = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        output = Path(args.output).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        if output.exists() and not args.overwrite:
            raise RiskAssessmentError(f"Output already exists: {output}")
        output.write_text(serialized, encoding="utf-8")
        print(f"RISK_ASSESSMENT={output}")
    if args.json or not args.output:
        print(serialized, end="")
    else:
        print(f"PROMPT_RISK={original['risk']}")
        print(f"GENERATION_RISK={report['generation_risk']}")
        print("NOTICE=Прогноз риска, не гарантия и не средство обхода ограничений.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Assess prompt and reference rejection risk on the D1-D10 scale.")
    parser.add_argument("--text", default="", help="Prompt text to assess.")
    parser.add_argument("--prompt-file", default="", help="UTF-8 prompt file to assess.")
    parser.add_argument("--revised-text", default="", help="Optional revised prompt for before/after recalculation.")
    parser.add_argument("--revised-prompt-file", default="", help="Optional UTF-8 revised prompt file.")
    parser.add_argument(
        "--reference",
        action="append",
        default=[],
        metavar="PATH::D5::+1D::REASON",
        help="Visually assessed reference. Repeat for every image that will be attached.",
    )
    parser.add_argument("--lexicon", type=Path, default=DEFAULT_LEXICON)
    parser.add_argument("--output", default="", help="Write the complete JSON assessment to this path.")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.set_defaults(handler=command_assess)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        args.handler(args)
        return 0
    except (RiskAssessmentError, OSError, json.JSONDecodeError) as error:
        print(f"ERROR={error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
