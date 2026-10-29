from pathlib import Path
from argparse import Namespace
import hashlib

import pytest

from tools import style_pack_manager as manager
from tools import task_execution_guard as guard


def test_child_prompt_requires_exact_closed_garment_and_rejects_adult_topology():
    boy = {
        "age_group": "CHILD",
        "subject_anatomy": "MALE_ANATOMY",
        "required_garment": "opaque age-appropriate T-shirt and shorts",
    }
    girl = {
        "age_group": "CHILD",
        "subject_anatomy": "FEMALE_ANATOMY",
        "required_garment": "opaque age-appropriate T-shirt and skirt",
    }
    manager.validate_character_base_executable_prompt("A boy wearing an opaque age-appropriate T-shirt and shorts.", boy)
    manager.validate_character_base_executable_prompt("A girl wearing an opaque age-appropriate T-shirt and skirt.", girl)
    with pytest.raises(manager.StylePackError, match="must explicitly require"):
        manager.validate_character_base_executable_prompt("A boy wearing a swimsuit.", boy)
    for incomplete in ("A boy wearing an opaque age-appropriate T-shirt only.", "A boy wearing opaque shorts only."):
        with pytest.raises(manager.StylePackError, match="must explicitly require"):
            manager.validate_character_base_executable_prompt(incomplete, boy)
    with pytest.raises(manager.StylePackError, match="must explicitly require"):
        manager.validate_character_base_executable_prompt("A girl wearing a closed one-piece swimsuit.", girl)
    with pytest.raises(manager.StylePackError, match="adult or exposed-topology"):
        manager.validate_character_base_executable_prompt("A boy wearing an opaque age-appropriate T-shirt and shorts; no swimsuit.", boy)
    mismatched = {**boy, "required_garment": "opaque age-appropriate T-shirt and skirt"}
    with pytest.raises(manager.StylePackError, match="wrong required garment"):
        manager.validate_character_base_executable_prompt("A boy wearing an opaque age-appropriate T-shirt and shorts.", mismatched)


def test_russian_child_gender_words_resolve_without_mixing_labels():
    assert manager.prompt_target_anatomy("Пелун — мальчик, ребёнок.")[0] == "MALE_ANATOMY"
    assert manager.prompt_target_anatomy("Пелун — девочка, ребёнок.")[0] == "FEMALE_ANATOMY"
    assert manager.prompt_target_anatomy("Пелун — мальчик и девочка.")[0] == "UNKNOWN"


def test_age_contract_rejects_unknown_or_adult_minor_conflict_and_keeps_adults():
    with pytest.raises(manager.StylePackError, match="requires explicit"):
        manager.resolve_character_base_contract(Namespace(character_age_group="", adult_character=False), {"target_anatomy": "MALE_ANATOMY"})
    with pytest.raises(manager.StylePackError, match="conflicts"):
        manager.resolve_character_base_contract(Namespace(character_age_group="CHILD", adult_character=True), {"target_anatomy": "MALE_ANATOMY"})
    adult = manager.resolve_character_base_contract(
        Namespace(character_age_group="ADULT", adult_character=True), {"target_anatomy": "MALE_ANATOMY"}
    )
    assert adult["adult_swimwear"] is True
    assert "adult_swimwear_ladder" not in adult
    assert adult["adult_anthropometry"] is True


def test_prompt_led_child_geometry_has_no_adult_head_count_or_shape_locks():
    child_height = manager.parse_head_height_contract("CHILD_AGE_APPROPRIATE", required=True, child_character=True)
    assert child_height == {
        "mode": "CHILD_AGE_APPROPRIATE", "source": "PROMPT_BODY_SPEC",
        "numeric_head_count": None, "adult_anthropometric_bounds": False,
    }
    with pytest.raises(manager.StylePackError, match="only for an explicitly identified CHILD"):
        manager.parse_head_height_contract("CHILD_AGE_APPROPRIATE", required=True, child_character=False)
    adult_height = manager.parse_head_height_contract("7.2-7.6", required=True, child_character=False)
    assert adult_height == {"mode": "EXPLICIT_RANGE", "minimum": 7.2, "maximum": 7.6}


def test_classified_subject_label_is_hash_bound_and_rejects_changed_source(tmp_path: Path):
    paths = manager.make_paths(tmp_path, "TEST")
    paths.pack.mkdir(parents=True)
    face = paths.pack / "01_WORK" / "FACE_CROPS" / "FACE_CORE_Pelun.png"
    face.parent.mkdir(parents=True)
    face.write_bytes(b"reviewed face source")
    row = {field: "" for field in manager.REFERENCE_FIELDS}
    row.update({
        "stored_relative_path": face.relative_to(paths.pack).as_posix(),
        "subject_label": "MALE", "subject_label_evidence": "Direct visual review.",
        "subject_label_sha256": hashlib.sha256(face.read_bytes()).hexdigest(),
        "subject_label_status": "REVIEWED", "status": "TEST", "user_approved": "NO",
    })
    manager.write_csv(paths.references, manager.REFERENCE_FIELDS, [row])
    assert manager.style_reference_compatibility_metadata(paths, face)["subject_label"] == "MALE"
    face.write_bytes(b"changed face source")
    with pytest.raises(manager.StylePackError, match="exact current image hash"):
        manager.style_reference_compatibility_metadata(paths, face)


@pytest.mark.parametrize(
    ("anatomy", "garment"),
    [
        ("MALE_ANATOMY", "opaque age-appropriate T-shirt and shorts"),
        ("FEMALE_ANATOMY", "opaque age-appropriate T-shirt and skirt"),
    ],
)
def test_child_workflow_preserves_five_stages_and_closed_garment(anatomy, garment):
    stages = manager.build_multistage_attachment_plan(
        {"style": [{"path": "style.png", "sha256": "a" * 64}],
         "primary_face": [{"path": "face.png", "sha256": "b" * 64}]},
        [], 5, "CHARACTER_BASE", "CHILD", garment,
    )
    assert [stage["stage_id"] for stage in stages] == list(guard.CHARACTER_BASE_STAGES)
    for stage in stages:
        assert garment in stage["purpose"]
    for stage in stages[1:4]:
        assert {"SAFE_COVERAGE", "CLOTHING_TOPOLOGY", "CLOTHING"}.issubset(stage["required_qa"])
    assert stages[4]["required_qa"][-3:] == ["SAFE_COVERAGE", "CLOTHING_TOPOLOGY", "CLOTHING"]


def test_guard_accepts_child_exact_call_without_adult_ladder_and_rejects_override():
    contract = {
        "age_group": "CHILD", "subject_anatomy": "MALE_ANATOMY",
        "required_garment": "opaque age-appropriate T-shirt and shorts",
        "coverage_policy": "OPAQUE_AGE_APPROPRIATE_CLOSED_COVERAGE",
        "adult_swimwear_ladder": False, "adult_anthropometry": False,
    }
    state = {
        "required_stages": [{"id": "02_PHYSIQUE_FRONT", "status": "PENDING"}],
        "character_base_contract": contract,
    }
    plan = {"character_base_contract": contract}
    call = {"prompt": {"text": "Child in opaque age-appropriate T-shirt and shorts."}}
    assert guard.validate_swimwear_execution_start(
        state, stage="02_PHYSIQUE_FRONT", swimwear_rung=None,
        user_swimwear_override=False, user_override_evidence="", plan=plan, execution_call=call,
    ) is None
    with pytest.raises(guard.GuardError, match="forbid adult swimwear rungs"):
        guard.validate_swimwear_execution_start(
            state, stage="02_PHYSIQUE_FRONT", swimwear_rung="CUSTOM",
            user_swimwear_override=True, user_override_evidence="user quote", plan=plan, execution_call=call,
        )
    with pytest.raises(guard.GuardError, match="omits the exact required"):
        guard.validate_swimwear_execution_start(
            state, stage="02_PHYSIQUE_FRONT", swimwear_rung=None,
            user_swimwear_override=False, user_override_evidence="", plan=plan,
            execution_call={"prompt": {"text": "Child in an opaque age-appropriate T-shirt only."}},
        )
    with pytest.raises(guard.GuardError, match="adult or exposed-topology"):
        guard.validate_swimwear_execution_start(
            state, stage="02_PHYSIQUE_FRONT", swimwear_rung=None,
            user_swimwear_override=False, user_override_evidence="", plan=plan,
            execution_call={"prompt": {"text": "Child in opaque age-appropriate T-shirt and shorts; exposed torso."}},
        )
    with pytest.raises(guard.GuardError, match="adult or exposed-topology"):
        guard.validate_swimwear_execution_start(
            state, stage="02_PHYSIQUE_FRONT", swimwear_rung=None,
            user_swimwear_override=False, user_override_evidence="", plan=plan,
            execution_call={"prompt": {"text": "Child in opaque age-appropriate T-shirt and shorts; swimsuit."}},
        )


def test_scope_change_adds_required_stages_without_losing_original_stage(tmp_path: Path):
    path = tmp_path / "EXECUTION_GUARD.json"
    guard.create_guard(
        path, request_id="minor-base", goal="Create the requested character",
        deliverable="Requested scene", required_stages=("SCENE",),
    )
    added = list(guard.CHARACTER_BASE_STAGES)
    state = guard.checkpoint(
        path, event="SCOPE_CHANGE", summary="User added the five character-base outputs.",
        user_approved_scope_change=True, add_required_stages=added,
    )
    assert [row["id"] for row in state["required_stages"]] == ["SCENE", *added]
    with pytest.raises(guard.GuardError, match="active or UNKNOWN"):
        state["attempts"] = [{"status": "UNKNOWN"}]
        from unittest.mock import patch

        with patch.object(guard, "load_guard", return_value=state):
            guard.checkpoint(
                path, event="SCOPE_CHANGE", summary="Cannot add during unknown work.",
                user_approved_scope_change=True, add_required_stages=("MORE",),
            )
