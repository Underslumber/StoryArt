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


class RiskAssessmentError(RuntimeError):
    pass


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
        "safer_formulation_suggestions_ru": list(dict.fromkeys(suggestions)),
    }


def parse_reference_spec(value: str) -> dict[str, object]:
    parts = value.split(REFERENCE_SPEC_SEPARATOR, 3)
    if len(parts) != 4:
        raise RiskAssessmentError(
            "Reference must use PATH::D1-D10::-2D..+2D::REASON. "
            "The rating must cover both image content and its likely influence when attached."
        )
    path_text, risk_text, impact_text, reason = (part.strip() for part in parts)
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
    references = [parse_reference_spec(value) for value in args.reference]
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
