"""Source-bound rendering authority and comparative QA; no image scoring or generation."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

try:
    from tools.style_pack_common import StylePackError
except ModuleNotFoundError:
    from style_pack_common import StylePackError

PLAN_VERSION = 7
STRICT_REVIEW_VERSION = 8
CRITERIA = ("mark_scale", "edges", "value_groups", "colour_structure", "materials", "detail_hierarchy")
BEGIN = "[STYLE_RENDERING_AUTHORITY]"
END = "[/STYLE_RENDERING_AUTHORITY]"


def build_style_contract(plan: dict) -> dict | None:
    selected = plan.get("selected_references", {})
    styles = selected.get("style", []) if isinstance(selected, dict) else []
    styles = styles if isinstance(styles, list) else [styles]
    if not styles:
        return None  # Explicit GENERATOR_DEFAULT is not a project style.
    sources = []
    reviews = plan.get("selected_source_reviews", [])
    for source in styles:
        if not isinstance(source, dict) or not source.get("path"):
            raise StylePackError("STYLE authority requires selected source records.")
        path = str(Path(source["path"]).resolve())
        digest = str(source.get("sha256", "")).lower()
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise StylePackError("STYLE authority requires an exact source SHA-256.")
        matches = [r for r in reviews if isinstance(r, dict)
                   and r.get("role") == "STYLE" and r.get("path")
                   and str(Path(r["path"]).resolve()) == path and r.get("sha256") == digest
                   and r.get("review_outcome") == "PASS" and r.get("view") == "FULL_RESOLUTION"]
        if len(matches) != 1:
            raise StylePackError("STYLE authority requires one exact-hash full-resolution source review.")
        review = matches[0]
        if any(not isinstance(review.get(key), str) or not review[key].strip() for key in ("review_findings", "limitations")):
            raise StylePackError("STYLE authority requires observed rendering findings and limitations.")
        sources.append({"path": path, "sha256": digest,
                        "observed_rendering": review["review_findings"], "limitations": review["limitations"]})
    if len({(s["path"], s["sha256"]) for s in sources}) != len(sources):
        raise StylePackError("Duplicate STYLE authority source.")
    return {"version": 2 if int(plan.get("schema_version", 0)) >= STRICT_REVIEW_VERSION else 1,
            "style_name": plan.get("style_name"), "requested_fidelity": plan.get("fidelity"),
            "fidelity_is_measured": False, "sources": sources,
            "authority": "RENDERING_ONLY", "criteria": list(CRITERIA),
            "preserve": ["identity", "physique", "pose", "coverage", "garment_design", "composition"],
            "qa": "SOURCE_COMPARISON_NOT_PALETTE_OR_FACE_ALONE"}


def style_prompt_block(contract: dict, slots: list[dict] | None = None) -> str:
    # Numbers are supplied only by the actual resolved manifest, never guessed.
    bindings = {(s["path"], s["sha256"]) for s in contract["sources"]}
    numbers = [str(s["slot"]) for s in (slots or [])
               if "STYLE" in s.get("active_roles", []) and (str(Path(s["path"]).resolve()), s["sha256"]) in bindings]
    authority = "attached STYLE source(s)" + (" at image slot(s) " + ", ".join(numbers) if numbers else "")
    observations = "\n".join(f"Rendering observation for {Path(s['path']).name}: {s['observed_rendering']}"
                             for s in contract["sources"])
    if contract.get("version", 1) >= 2 and slots:
        # The generator receives images, not the JSON manifest. Expose every
        # physical slot's actual roles so other visual manners cannot silently
        # compete with STYLE, especially photographs used only for body build.
        role_map = []
        for slot in slots:
            if slot.get("physically_attach") is not True:
                continue
            roles = slot.get("active_roles", [])
            boundary = "rendering authority" if "STYLE" in roles else "designated content only; do not copy its rendering"
            role_map.append(f"Image {slot['slot']}: {', '.join(roles)} — {boundary}.")
        observations = "Exact physical attachment roles:\n" + "\n".join(role_map) + "\n" + observations
    return (f"{BEGIN}\nGoal: render the requested deliverable below with all its invariants. "
            "Identity, physique, pose and garment construction come from their own designated sources and user text. "
            f"Only the {authority} define the rendering of the whole image in {contract['style_name']}. "
            "Match their actual mark scale relative to forms, edge variation, light/shadow grouping, colour structure, "
            "material treatment and detail hierarchy across every visible region. Identity/body/clothes inputs do not "
            "override rendering; STYLE does not transfer their subject, costume, anatomy or scene. "
            "Match the selected style itself, not a generic style label or palette alone; preserve all requested design details. "
            f"The selected fidelity {contract['requested_fidelity']} is a target, not a measured score.\n"
            f"{observations}\n{END}")


def validate_style_contract(plan: dict, call: dict | None = None, *, check_files: bool = False) -> None:
    if int(plan.get("schema_version", 0)) < PLAN_VERSION:
        return  # Existing immutable attempts retain their historical contract.
    expected = build_style_contract(plan)
    if plan.get("style_rendering_contract") != expected:
        raise StylePackError("STYLE rendering contract differs from its reviewed selected sources.")
    if expected is None:
        return
    if check_files:
        for source in expected["sources"]:
            p = Path(source["path"])
            if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest() != source["sha256"]:
                raise StylePackError("STYLE authority source is missing or changed.")
    if call is not None:
        slots = call.get("slots", [])
        physical = [s for s in slots if isinstance(s, dict) and "STYLE" in s.get("active_roles", [])]
        wanted = {(s["path"], s["sha256"]) for s in expected["sources"]}
        actual = {(str(Path(s["path"]).resolve()), s.get("sha256")) for s in physical}
        if actual != wanted or len(physical) != len(wanted) or any(s.get("physically_attach") is not True for s in physical):
            raise StylePackError("Every reviewed STYLE authority must be physically attached in its exact STYLE role.")
        prompt = call.get("prompt", {}).get("text", "")
        # The prompt can have numbered or unnumbered authority, but must have
        # exactly the one canonical block that was risk-assessed and executed.
        blocks = [style_prompt_block(expected), style_prompt_block(expected, slots)]
        if not any(prompt.startswith(b + "\n\n") for b in blocks) or prompt.count(BEGIN) != 1 or prompt.count(END) != 1:
            raise StylePackError("Exact prompt is missing its canonical STYLE rendering authority.")


def render_style_authority(plan: dict, text: str, slots: list[dict] | None = None) -> str:
    if int(plan.get("schema_version", 0)) < PLAN_VERSION:
        return text
    validate_style_contract(plan)
    contract = plan.get("style_rendering_contract")
    if contract is None:
        return text
    if slots is None and isinstance(plan.get("execution_call"), dict):
        # A raw-prompt READY retry must retain the already risk-assessed block
        # format (numbered wrapper call vs unnumbered direct manager call).
        stored = plan["execution_call"]
        stored_slots = stored.get("slots", [])
        if stored.get("prompt", {}).get("text", "").startswith(style_prompt_block(contract, stored_slots) + "\n\n"):
            slots = stored_slots
    if BEGIN in text or END in text:
        valid = [style_prompt_block(contract), style_prompt_block(contract, slots)]
        if any(text.startswith(block + "\n\n") for block in valid) and text.count(BEGIN) == text.count(END) == 1:
            return text
        raise StylePackError("Caller-supplied STYLE authority differs from the prepared contract.")
    return style_prompt_block(contract, slots) + "\n\n" + text


def required_style_regions(plan: dict, stage: str) -> list[str]:
    regions = ["materials", "background"]
    if stage in {"01_FACE_IDENTITY", "02_PHYSIQUE_FRONT", "03_PHYSIQUE_SIDE", "05_CHARACTER_ASSEMBLY"} or (
        stage != "04_PHYSIQUE_BACK" and plan.get("face_review", {}).get("face_visible")
    ):
        regions.append("face")
    canvas = plan.get("canvas_contract", {})
    if canvas.get("full_figure") or (
        int(plan.get("schema_version", 0)) >= STRICT_REVIEW_VERSION and canvas.get("body_visible") is True
    ) or stage in {
        "02_PHYSIQUE_FRONT", "03_PHYSIQUE_SIDE", "04_PHYSIQUE_BACK", "05_CHARACTER_ASSEMBLY"
    }:
        regions.append("body")
    # Clothes/material review is always present even without a separate clothes input.
    return regions


def validate_style_review(plan: dict, report: dict, stage: str) -> dict | None:
    if int(plan.get("schema_version", 0)) < PLAN_VERSION:
        return None
    validate_style_contract(plan, plan.get("execution_call"))
    if plan.get("style_rendering_contract") is None:
        return None
    review = report.get("style_review")
    if not isinstance(review, dict) or review.get("status") not in {"PASS", "FAIL", "NOT_CHECKED"}:
        raise StylePackError("Visual review requires a source-comparative style_review, not only STYLE=PASS.")
    expected_sources = [{k: s[k] for k in ("path", "sha256")} for s in plan["style_rendering_contract"]["sources"]]
    if review.get("sources") != expected_sources:
        raise StylePackError("Style review must compare the exact executed STYLE sources and hashes.")
    if "measured_fidelity" in review:
        raise StylePackError("A style attestation cannot claim measured numerical fidelity.")

    def validate_rows(rows: object, expected: list[str] | tuple[str, ...], label: str) -> list[str]:
        if not isinstance(rows, dict) or set(rows) != set(expected):
            raise StylePackError(f"Style review {label} must cover: {', '.join(expected)}.")
        statuses = []
        for key, row in rows.items():
            if not isinstance(row, dict) or row.get("status") not in {"PASS", "FAIL", "NOT_CHECKED", "NOT_APPLICABLE"}:
                raise StylePackError(f"Style review {label}.{key} requires an explicit status.")
            evidence = row.get("evidence")
            if not isinstance(evidence, str) or len(evidence.strip()) < 20:
                raise StylePackError(f"Style review {label}.{key} requires a specific source/output comparison.")
            if row["status"] == "NOT_APPLICABLE" and (not isinstance(row.get("reason"), str) or len(row["reason"].strip()) < 20):
                raise StylePackError(f"Style review {label}.{key} NOT_APPLICABLE needs a substantive reason.")
            if (int(plan.get("schema_version", 0)) >= STRICT_REVIEW_VERSION
                    and label == "criteria" and row["status"] == "NOT_APPLICABLE" and key != "mark_scale"):
                raise StylePackError(
                    f"Style review criteria.{key} cannot be NOT_APPLICABLE; "
                    "use NOT_CHECKED when the source or output is insufficient."
                )
            if label == "regions" and key in {"face", "body"} and row["status"] == "NOT_APPLICABLE":
                raise StylePackError(f"Visible {key} style cannot be NOT_APPLICABLE.")
            statuses.append(row["status"])
        return statuses

    statuses = validate_rows(review.get("criteria"), CRITERIA, "criteria")
    if all(s == "NOT_APPLICABLE" for s in statuses):
        raise StylePackError("STYLE requires at least one applicable comparative rendering criterion.")
    regions = review.get("regions")
    statuses += validate_rows(regions, required_style_regions(plan, stage), "regions")
    if all(s == "NOT_APPLICABLE" for s in statuses):
        raise StylePackError("A physical STYLE contract cannot have every comparison NOT_APPLICABLE.")
    derived = "FAIL" if "FAIL" in statuses else "NOT_CHECKED" if "NOT_CHECKED" in statuses else "PASS"
    if review["status"] != derived:
        raise StylePackError("Style review status contradicts its criteria/region findings.")
    return review
