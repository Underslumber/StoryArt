from argparse import Namespace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tools.style_pack_manager import (
    StylePackError,
    build_generation_workflow,
    build_parser,
    build_scene_contract,
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

    def test_prepare_parser_defaults_to_visible_numbered_startup_menu(self) -> None:
        args = build_parser().parse_args([
            "prepare-generation", "--style-name", "TEST", "--request-id", "menu-default", "--fidelity", "90",
        ])
        self.assertEqual(args.startup_selection_mode, "USER_CONFIRMATION")
        self.assertEqual(args.startup_menu_surface, "TEXT_NUMBERED_MENU")

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
        )
        failed, stage_id, required = evaluate_generation_qa(plan, qa)
        self.assertEqual(failed, [])
        self.assertEqual(stage_id, "SINGLE_PASS")
        self.assertIn("DESKTOP_USABILITY", required)
        self.assertIn("ARTIFACT_INTEGRITY", required)
        self.assertNotIn("FACE_GEOMETRY", required)


if __name__ == "__main__":
    unittest.main()
