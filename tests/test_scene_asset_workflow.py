from argparse import Namespace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import json
import contextlib
import io
from unittest.mock import patch

from tools.style_pack_manager import (
    StylePackError,
    build_generation_workflow,
    build_body_proportion_contract,
    build_parser,
    build_scene_contract,
    build_scene_prompt_source_contract,
    evaluate_generation_qa,
    StylePaths,
    validate_plan_reference,
)


def scene_args(**overrides: object) -> Namespace:
    values: dict[str, object] = {
        "scene_kind": "ARTIFACT",
        "scene_output_use": "WALLPAPER",
        "text_safe_zone": "NONE",
        "subject_reference": "",
        "scene_subject_from_prompt": True,
        "character_assembly": "",
        "primary_face": "",
        "supporting_face": "",
        "expression_reference": "",
        "body_reference": "",
        "pose_reference": "",
        "clothes_reference": "",
        "aux_body": [],
        "coverage_front_reference": "",
        "coverage_side_reference": "",
        "coverage_back_reference": "",
    }
    values.update(overrides)
    return Namespace(**values)


def reference(name: str) -> dict[str, object]:
    return {"path": name, "sha256": name, "active_roles": []}


class SceneContractTests(unittest.TestCase):
    def test_approved_character_scene_marks_only_missing_optional_roles_as_prompt_sources(self) -> None:
        sources = build_scene_prompt_source_contract("SCENE", "CHAR_001", {
            "POSE": "", "CLOTHES": "", "LIGHTING": "", "BACKGROUND": "", "COMPOSITION": "camera.png",
        })
        self.assertEqual(set(sources), {"POSE", "CLOTHES", "ACCESSORY", "LIGHTING", "BACKGROUND"})
        self.assertTrue(all(row == {
            "source": "EXACT_EXECUTABLE_PROMPT",
            "evidence_required": "USER_SPECIFIED_SCENE_TEXT",
        } for row in sources.values()))
        self.assertEqual(build_scene_prompt_source_contract("SCENE", "NONE", {}), {})
        self.assertEqual(build_scene_prompt_source_contract("CHARACTER_BASE", "CHAR_001", {}), {})

    def test_character_resolver_reports_matched_but_invalid_approved_profile(self) -> None:
        from tools import style_pack_manager as manager

        with TemporaryDirectory() as folder:
            workspace = Path(folder)
            paths = manager.make_paths(workspace, "TEST")
            paths.generations.mkdir(parents=True)
            profile = paths.generations / "CHARACTER_PROFILE.yaml"
            profile.write_text("character_id: CHAR_001\nstatus: APPROVED\n", encoding="utf-8")
            manager.write_csv(paths.character_registry, manager.CHARACTER_FIELDS, [{
                "character_id": "CHAR_001", "name": "Shance", "approved_base": "missing-base.png",
                "profile_path": str(profile), "face_references": "", "body_references": "",
                "status": "APPROVED",
            }])
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                manager.command_resolve_character(Namespace(workspace=workspace, name="Shance", json=True))
            result = json.loads(output.getvalue())
            self.assertEqual(result["status"], "FOUND_BUT_INVALID")
            self.assertEqual(result["invalid_matches"][0]["status"], "INVALID_APPROVED_PROFILE")
            self.assertIn("no registered approved base image", result["invalid_matches"][0]["issue"])

    def test_scene_cli_defaults_to_no_character_and_no_body_fields(self) -> None:
        args = build_parser().parse_args(
            [
                "prepare-generation",
                "--style-name",
                "TEST",
                "--request-id",
                "scene",
                "--fidelity",
                "90",
                "--risk-assessment",
                "risk.json",
                "--aux-body-decision",
                "DECLINED",
            ]
        )
        self.assertEqual(args.character_id, "NONE")
        self.assertEqual(args.framing, "")
        self.assertEqual(args.dominant_body_source, "")

    def test_prepare_parser_defaults_to_native_startup_menu(self) -> None:
        args = build_parser().parse_args([
            "prepare-generation", "--style-name", "TEST", "--request-id", "menu-default", "--fidelity", "90",
        ])
        self.assertEqual(args.startup_selection_mode, "NEW")
        self.assertEqual(args.startup_menu_surface, "")

    def test_prepare_parser_preserves_explicit_text_fallback(self) -> None:
        args = build_parser().parse_args([
            "prepare-generation", "--style-name", "TEST", "--request-id", "menu-fallback", "--fidelity", "90",
            "--startup-selection-mode", "USER_CONFIRMATION", "--startup-menu-surface", "TEXT_NUMBERED_MENU",
        ])
        self.assertEqual(args.startup_selection_mode, "USER_CONFIRMATION")
        self.assertEqual(args.startup_menu_surface, "TEXT_NUMBERED_MENU")

    def test_scene_adapter_instructions_are_matrix_first_and_selected_source_only(self) -> None:
        from tools.storyart_orchestrator import adapter_skill_markdown

        prompt = adapter_skill_markdown({
            "style_name": "RIOT LOL SPLASH",
            "slug": "riot-lol-splash",
            "pack_path": "STYLE_PACK_RIOT",
            "generations_path": "GENERATIONS_RIOT",
        })
        self.assertLess(prompt.index("written profile"), prompt.index("matrix or contact sheet"))
        self.assertLess(prompt.index("matrix or contact sheet"), prompt.index("selected full-resolution"))
        self.assertNotIn("Query complete file lists", prompt)

    def test_selected_style_refresh_preserves_other_adapters_and_index_entries(self) -> None:
        import json
        import tempfile
        from unittest.mock import patch
        from tools import storyart_orchestrator as orchestrator

        with patch.object(orchestrator, "ensure_inside_project", side_effect=lambda path: path), \
             patch.object(orchestrator, "relative_project_path", side_effect=lambda path: str(path)):
            with tempfile.TemporaryDirectory() as folder:
                root = Path(folder) / "adapters"
                selected = root / "storyart-style-selected"
                other = root / "storyart-style-other"
                (selected / "references").mkdir(parents=True)
                (selected / "agents").mkdir()
                (other / "references").mkdir(parents=True)
                (other / "agents").mkdir()
                (other / "SKILL.md").write_text("keep me", encoding="utf-8")
                (other / "agents" / "openai.yaml").write_text("keep yaml", encoding="utf-8")
                (other / "references" / "style.json").write_text("{}", encoding="utf-8")
                stale_style = {
                    "style_name": "Selected Style", "slug": "selected", "pack_path": "PACK_SELECTED",
                    "generations_path": "GEN_SELECTED", "local_readiness": "READY", "can_generate": True,
                    "management": {}, "source_images": [], "work_images": [], "characters": [],
                }
                (selected / "references" / "style.json").write_text(json.dumps(stale_style), encoding="utf-8")
                index = {
                    "custom": "preserve", "styles": [
                        {"style_name": "Selected Style", "skill_name": "storyart-style-selected", "path": str(selected),
                         "pack_path": "PACK_SELECTED", "local_readiness": "READY", "can_generate": True},
                        {"style_name": "Other Style", "skill_name": "storyart-style-other", "path": str(other),
                         "pack_path": "PACK_OTHER", "local_readiness": "READY", "can_generate": True},
                    ],
                }
                (root).mkdir(parents=True, exist_ok=True)
                (root / "index.json").write_text(json.dumps(index), encoding="utf-8")
                other_before = {p.relative_to(other): p.read_bytes() for p in other.rglob("*") if p.is_file()}
                live_style = dict(stale_style, pack_path="PACK_SELECTED_CURRENT", generations_path="GEN_SELECTED_CURRENT")
                with patch.object(orchestrator, "query_ready_styles", return_value=[live_style]):
                    result = orchestrator.build_style_skills(root, "Selected Style")
                other_after = {p.relative_to(other): p.read_bytes() for p in other.rglob("*") if p.is_file()}
                self.assertEqual(other_after, other_before)
                self.assertEqual(result["custom"], "preserve")
                self.assertEqual([entry["style_name"] for entry in result["styles"]], ["Selected Style", "Other Style"])
                refreshed = json.loads((selected / "references" / "style.json").read_text(encoding="utf-8"))
                self.assertEqual(refreshed["pack_path"], "PACK_SELECTED_CURRENT")

    def test_character_free_scene_has_no_character_reference_contract(self) -> None:
        contract = build_scene_contract(scene_args(), "SCENE", "NONE")
        self.assertTrue(contract["applicable"])
        self.assertFalse(contract["has_character"])
        self.assertEqual(contract["scene_kind"], "ARTIFACT")
        self.assertIn("NO_CHARACTER_REFERENCE_FAMILIES", contract["character_policy"])

    def test_character_free_scene_rejects_every_character_reference_input(self) -> None:
        forbidden_inputs = {
            "character_assembly": "assembly.png",
            "primary_face": "face.png",
            "supporting_face": "supporting-face.png",
            "expression_reference": "expression.png",
            "body_reference": "body.png",
            "pose_reference": "pose.png",
            "clothes_reference": "clothes.png",
            "aux_body": ["BR_0007=STAGING_ONLY"],
            "coverage_front_reference": "coverage-front.png",
            "coverage_side_reference": "coverage-side.png",
            "coverage_back_reference": "coverage-back.png",
        }
        for field, value in forbidden_inputs.items():
            with self.subTest(field=field):
                with self.assertRaisesRegex(StylePackError, "forbids character reference families"):
                    build_scene_contract(scene_args(**{field: value}), "SCENE", "NONE")

    def test_character_free_scene_still_rejects_approved_character_assembly(self) -> None:
        with self.assertRaisesRegex(StylePackError, "forbids character reference families"):
            build_scene_contract(scene_args(character_assembly="assembly.png"), "SCENE", "NONE")

    def test_promo_poster_requires_copy_safe_zone(self) -> None:
        with self.assertRaisesRegex(StylePackError, "text-safe-zone"):
            build_scene_contract(
                scene_args(scene_output_use="PROMO_POSTER", text_safe_zone="NONE"),
                "SCENE",
                "NONE",
            )

    def test_explicit_character_scene_remains_supported(self) -> None:
        contract = build_scene_contract(
            scene_args(scene_kind="LOCATION", scene_output_use="GENERAL_ART"),
            "SCENE",
            "CHAR_002",
        )
        self.assertTrue(contract["has_character"])
        self.assertEqual(contract["character_policy"], "EXPLICIT_APPROVED_CHARACTER")


class SceneWorkflowTests(unittest.TestCase):
    def test_character_body_can_follow_text_pose_without_pose_reference(self) -> None:
        args = Namespace(
            dominant_body_source="CHARACTER_BODY",
            target_pose_family="SEATED",
            body_source_coverage="FULL_BODY",
            body_source_pose_family="STANDING",
            framing="FULL_BODY",
            allow_body_identity_change=False,
            body_silhouette_notes="Preserve shoulders, torso, waist, hips, thighs, and leg-to-torso ratio.",
            body_height_heads="SOURCE_LOCK",
        )

        contract = build_body_proportion_contract(
            args,
            selected={"body": reference("approved-character-body")},
            auxiliary=[],
            is_new_character=False,
        )

        self.assertEqual(contract["dominant_source"], "CHARACTER_BODY")
        self.assertTrue(contract["pose_may_not_change_permanent_proportions"])

    def test_character_body_with_pose_reference_keeps_proportion_lock(self) -> None:
        args = Namespace(
            dominant_body_source="CHARACTER_BODY",
            target_pose_family="SEATED",
            body_source_coverage="FULL_BODY",
            body_source_pose_family="STANDING",
            framing="FULL_BODY",
            allow_body_identity_change=False,
            body_silhouette_notes="Preserve shoulders, torso, waist, hips, thighs, and leg-to-torso ratio.",
            body_height_heads="SOURCE_LOCK",
        )

        contract = build_body_proportion_contract(
            args,
            selected={
                "body": reference("approved-character-body"),
                "pose": reference("optional-pose-aid"),
            },
            auxiliary=[],
            is_new_character=False,
        )

        self.assertTrue(contract["pose_may_not_change_permanent_proportions"])

    def test_existing_character_still_requires_character_body_as_dominant(self) -> None:
        args = Namespace(
            dominant_body_source="PROMPT_BODY_SPEC",
            target_pose_family="SEATED",
            body_source_coverage="FULL_BODY",
            body_source_pose_family="STANDING",
            framing="FULL_BODY",
            allow_body_identity_change=False,
            body_silhouette_notes="Preserve all permanent proportions.",
            body_height_heads="SOURCE_LOCK",
        )

        with self.assertRaisesRegex(StylePackError, "must use CHARACTER_BODY"):
            build_body_proportion_contract(args, selected={}, auxiliary=[], is_new_character=False)

    def test_anonymous_scene_can_use_final_curated_body_contour_as_pose(self) -> None:
        with TemporaryDirectory() as folder:
            workspace = Path(folder)
            contour = (
                workspace
                / "BODY_REFERENCE_LIBRARY"
                / "02_LOCAL_ONLY"
                / "ANATOMY_CONTOUR_REFERENCE_LIBRARY"
                / "07_FINAL_CURATED"
                / "PRIMARY_PHYSIOLOGY"
                / "REAR"
                / "BC_BR_0075.png"
            )
            contour.parent.mkdir(parents=True)
            contour.write_bytes(b"pose")
            paths = StylePaths(
                workspace=workspace,
                style_name="TEST",
                slug="TEST",
                pack=workspace / "TEST_PROJECT_PACK",
                generations=workspace / "TEST_GENERATIONS",
            )

            selected = validate_plan_reference(
                paths,
                str(contour),
                "POSE",
                allow_curated_body_contour=True,
            )

            self.assertEqual(selected["status"], "FINAL_CURATED_BODY_CONTOUR")
            self.assertIn("BODY_CONTOUR", selected["inferred_roles"])

    def test_character_free_scene_never_builds_hidden_staging(self) -> None:
        args = Namespace(
            reference_workflow="AUTO",
            generation_purpose="SCENE",
            character_id="NONE",
            attachment_limit=5,
        )
        workflow = build_generation_workflow(
            args,
            {
                "style": [reference("style")],
                "subject": reference("subject"),
                "lighting": reference("lighting"),
                "background": reference("background"),
                "composition": reference("composition"),
            },
            [],
        )
        self.assertEqual(workflow["mode"], "SINGLE_PASS")
        self.assertTrue(workflow["unrequested_staging_forbidden"])
        self.assertNotIn("stages", workflow)

    def test_character_free_scene_blocks_more_than_five_attachments(self) -> None:
        args = Namespace(
            reference_workflow="AUTO",
            generation_purpose="SCENE",
            character_id="NONE",
            attachment_limit=5,
        )
        with self.assertRaisesRegex(StylePackError, "needs 6 physical attachments"):
            build_generation_workflow(
                args,
                {
                    "style": [reference("style-a"), reference("style-b")],
                    "subject": reference("subject"),
                    "lighting": reference("lighting"),
                    "background": reference("background"),
                    "composition": reference("composition"),
                },
                [],
            )

    def test_character_bearing_scene_does_not_auto_expand_attachment_overflow(self) -> None:
        args = Namespace(
            reference_workflow="AUTO",
            generation_purpose="SCENE",
            character_id="CHAR_001",
            attachment_limit=5,
        )
        selected = {
            name: reference(name)
            for name in ("style", "primary_face", "body", "pose", "clothes", "background")
        }
        with self.assertRaisesRegex(StylePackError, "cannot auto-expand into generated helper images"):
            build_generation_workflow(args, selected, [])


class SceneQaTests(unittest.TestCase):
    def test_wallpaper_artifact_requires_scene_specific_qa(self) -> None:
        plan = {
            "generation_purpose": "SCENE",
            "semantic_qa_schema": 4,
            "generation_workflow": {"mode": "SINGLE_PASS"},
            "face_review": {"face_visible": False},
            "canvas_contract": {"full_figure": False},
            "scene_contract": {
                "applicable": True,
                "has_character": False,
                "scene_kind": "ARTIFACT",
                "output_use": "WALLPAPER",
            },
        }
        qa = Namespace(
            stage_id="",
            qa_attachments="PASS",
            qa_canvas="PASS",
            qa_stage_layer="PASS",
            qa_face="NOT_CHECKED",
            qa_body_silhouette="NOT_CHECKED",
            qa_body_proportions="NOT_CHECKED",
            qa_style="PASS",
            qa_lighting="PASS",
            qa_background="PASS",
            qa_composition="PASS",
            qa_subject_accuracy="PASS",
            qa_no_unrequested_characters="PASS",
            qa_focal_hierarchy="PASS",
            qa_distance_readability="PASS",
            qa_desktop_usability="PASS",
            qa_poster_readability="NOT_CHECKED",
            qa_copy_safe_area="NOT_CHECKED",
            qa_depth_and_scale="NOT_CHECKED",
            qa_phenomenon_causality="NOT_CHECKED",
            qa_artifact_integrity="PASS",
            visual_anatomy_status="PASS",
            visual_defects_status="PASS",
            visual_prompt_status="PASS",
        )
        failed, stage_id, required = evaluate_generation_qa(plan, qa)
        self.assertEqual(failed, [])
        self.assertEqual(stage_id, "SINGLE_PASS")
        self.assertIn("DESKTOP_USABILITY", required)
        self.assertIn("ARTIFACT_INTEGRITY", required)
        self.assertNotIn("FACE_GEOMETRY", required)


class OptionalPoseCommandTests(unittest.TestCase):
    def test_prepare_generation_without_pose_file_override_or_pool_review(self):
        from tests.test_generation_workflow_integration import GenerationWorkflowIntegrationTests
        fixture = GenerationWorkflowIntegrationTests()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        fixture._classify("unused-pose.png", "POSE_CORE", (1, 2, 3))
        original = fixture._cli

        def without_pose_override(script, *args, **kwargs):
            args = list(args)
            if args and args[0] == "prepare-generation":
                index = args.index("POSE")
                self.assertEqual(args[index - 1], "--override")
                del args[index - 1:index + 1]
            return original(script, *args, **kwargs)

        with patch.object(fixture, "_cli", side_effect=without_pose_override):
            _, plan_path = fixture._prepare_scene("optional-pose")
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        self.assertNotIn("pose", plan.get("selected_references", {}))
        self.assertEqual(plan["gate_status"], "PREPARED_AWAITING_EXECUTABLE_CALL")

    def test_prepare_call_approved_policy_allows_project_scene_reference(self):
        from tests.test_generation_workflow_integration import GenerationWorkflowIntegrationTests
        fixture = GenerationWorkflowIntegrationTests()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        background = fixture._classify("scene-background.png", "BACKGROUND_CORE", (4, 5, 6))
        original = fixture._cli

        def with_background(script, *args, **kwargs):
            args = list(args)
            if args and args[0] == "prepare-generation":
                index = args.index("BACKGROUND")
                del args[index - 1:index + 1]
                attestation = json.dumps({
                    "role": "BACKGROUND", "slot_role": "BACKGROUND", "path": str(background),
                    "view": "FULL_RESOLUTION", "outcome": "PASS",
                    "applicability": "scene background composition and palette",
                    "findings": "The selected source supports the requested background role.",
                    "limitations": "No subject identity or anatomy assessment.",
                })
                args.extend(("--background-reference", background, "--reviewed", "BACKGROUND=1", "--reviewed-source", attestation))
            return original(script, *args, **kwargs)

        with patch.object(fixture, "_cli", side_effect=with_background):
            guard, plan_path = fixture._prepare_scene("approved-scene")
        original_selections = fixture._user_selections_file

        def approved_selections(*args):
            path = original_selections(*args)
            data = json.loads(path.read_text(encoding="utf-8"))
            data["reference_policy"]["choice"] = "APPROVED_CHARACTER_REFERENCES"
            path.write_text(json.dumps(data), encoding="utf-8")
            return path

        with patch.object(fixture, "_user_selections_file", side_effect=approved_selections):
            call = fixture._resolve_and_prepare_call("approved-scene", guard, plan_path, "A stone artifact on the approved background.")
        self.assertTrue(any("BACKGROUND" in slot["active_roles"] for slot in call["slots"]))

    def test_approved_policy_checks_derived_lineage_and_rejects_unapproved_inputs(self):
        from tools import style_pack_manager as manager
        with TemporaryDirectory() as folder:
            paths = manager.make_paths(Path(folder), "TEST")
            identity = paths.generations / "01_APPROVED_CHARACTERS" / "CHAR_001"
            identity.mkdir(parents=True)
            source = identity / "identity.bin"
            source.write_bytes(b"synthetic identity metadata fixture")
            derived = paths.generations / "00_PENDING" / "request" / "derived.bin"
            derived.parent.mkdir(parents=True)
            derived.write_bytes(b"synthetic stage metadata fixture")
            selections = {"reference_policy": {"choice": "APPROVED_CHARACTER_REFERENCES"}, "character": {"choice": "CHAR_001"}}
            source_slot = {"path": str(source), "sha256": manager.sha256(source), "active_roles": ["BODY"]}
            snapshot = derived.parent / "snapshot.json"
            snapshot.write_text(json.dumps({"request_id": "request", "execution_call": {"slots": [source_slot]}}), encoding="utf-8")
            output = {"path": str(derived), "sha256": manager.sha256(derived), "plan_snapshot": str(snapshot), "plan_snapshot_sha256": manager.sha256(snapshot)}
            slot = {"path": str(derived), "sha256": manager.sha256(derived), "active_roles": ["BODY"]}
            with patch.object(manager, "character_folder", return_value=identity), patch.object(manager, "validated_stage_outputs", return_value={"STAGE": output}):
                manager.validate_reference_policy_slots(paths, {"request_id": "request"}, {"slots": [slot]}, selections)
                for bad_path in (Path(folder) / "user.bin", paths.workspace / manager.BODY_LIBRARY_NAME / "body.bin"):
                    bad = {"path": str(bad_path), "sha256": "unknown", "active_roles": ["BODY"]}
                    with self.subTest(path=bad_path), self.assertRaises(manager.StylePackError):
                        manager.validate_reference_policy_slots(paths, {"request_id": "request"}, {"slots": [source_slot, bad]}, selections)
                library_style = {"path": str(paths.workspace / manager.BODY_LIBRARY_NAME / "style.bin"), "sha256": "unknown", "active_roles": ["STYLE"]}
                with self.assertRaises(manager.StylePackError):
                    manager.validate_reference_policy_slots(paths, {"request_id": "request"}, {"slots": [source_slot, library_style]}, selections)
                snapshot.write_text(json.dumps({"request_id": "request", "execution_call": {"slots": [source_slot, {"path": str(Path(folder) / "user.bin"), "sha256": "unknown", "active_roles": ["BODY"]}]}}), encoding="utf-8")
                output["plan_snapshot_sha256"] = manager.sha256(snapshot)
                with self.assertRaises(manager.StylePackError):
                    manager.validate_reference_policy_slots(paths, {"request_id": "request"}, {"slots": [slot]}, selections)
                snapshot.write_text("tampered", encoding="utf-8")
                with self.assertRaises(manager.StylePackError):
                    manager.validate_reference_policy_slots(paths, {"request_id": "request"}, {"slots": [slot]}, selections)

    def test_body_library_selection_is_request_scoped_and_not_required_on_every_stage(self):
        from tools import style_pack_manager as manager
        with TemporaryDirectory() as folder:
            paths = manager.make_paths(Path(folder), "TEST")
            style = paths.pack / "style.png"
            style.parent.mkdir(parents=True, exist_ok=True)
            style.write_bytes(b"selected style")
            plan = {
                "request_id": "request",
                "startup_parameter_selection": {"resolved_parameters": {"aux_body_decision": "SELECTED"}},
            }
            selections = {
                "style": {"choice": "PROJECT_STYLE:TEST"},
                "reference_policy": {"choice": "BODY_LIBRARY_ONLY"},
                "character": {"choice": "NONE"},
            }
            first_stage_call = {"slots": [{
                "path": str(style), "sha256": manager.sha256(style), "active_roles": ["STYLE"],
            }]}
            # FACE_IDENTITY correctly has no physique library input; consent is
            # recorded once for the request and consumed only by relevant stages.
            manager.validate_reference_policy_slots(paths, plan, first_stage_call, selections)

    def test_stage_lineage_inherits_only_the_selected_reference_policy(self):
        from tools import style_pack_manager as manager
        with TemporaryDirectory() as folder:
            paths = manager.make_paths(Path(folder), "TEST")
            request = paths.generations / "00_PENDING" / "request"
            request.mkdir(parents=True)
            source = paths.workspace / manager.BODY_LIBRARY_NAME / "body.bin"
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_bytes(b"approved selected body source")
            derived = request / "front.png"
            derived.write_bytes(b"qa passed stage output")
            snapshot = request / "snapshot.json"
            library_slot = {"path": str(source), "sha256": manager.sha256(source), "active_roles": ["BODY"]}
            style_slot = {"path": str(paths.pack / "style.png"), "sha256": "a" * 64, "active_roles": ["STYLE"]}
            selections = {
                "style": {"choice": "PROJECT_STYLE:TEST"},
                "reference_policy": {"choice": "BODY_LIBRARY_ONLY"},
                "character": {"choice": "NONE"},
            }
            plan = {
                "request_id": "request",
                "startup_parameter_selection": {"resolved_parameters": {"aux_body_decision": "SELECTED"}},
            }
            snapshot.write_text(json.dumps({**plan, "execution_call": {"slots": [style_slot, library_slot]}}), encoding="utf-8")
            output = {
                "path": str(derived), "sha256": manager.sha256(derived),
                "plan_snapshot": str(snapshot), "plan_snapshot_sha256": manager.sha256(snapshot),
            }
            derived_slot = {"path": str(derived), "sha256": output["sha256"], "active_roles": ["BODY"]}
            with patch.object(manager, "validated_stage_outputs", return_value={"01_PHYSIQUE_FRONT": output}):
                manager.validate_reference_policy_slots(paths, plan, {"slots": [derived_slot]}, selections)
                selections["reference_policy"]["choice"] = "PROJECT_STYLE_ONLY"
                snapshot.write_text(json.dumps({**plan, "execution_call": {"slots": [style_slot]}}), encoding="utf-8")
                output["plan_snapshot_sha256"] = manager.sha256(snapshot)
                manager.validate_reference_policy_slots(paths, plan, {"slots": [style_slot, derived_slot]}, selections)
                selections["reference_policy"]["choice"] = "BODY_LIBRARY_ONLY"
                snapshot.write_text(json.dumps({
                    "request_id": "another-request", "execution_call": {"slots": [style_slot, library_slot]},
                    "startup_parameter_selection": plan["startup_parameter_selection"],
                }), encoding="utf-8")
                output["plan_snapshot_sha256"] = manager.sha256(snapshot)
                with self.assertRaises(manager.StylePackError):
                    manager.validate_reference_policy_slots(paths, plan, {"slots": [derived_slot]}, selections)


if __name__ == "__main__":
    unittest.main()
