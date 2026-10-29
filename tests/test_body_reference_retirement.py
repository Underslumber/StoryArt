from argparse import Namespace
from contextlib import redirect_stdout
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest

from tools.body_reference_type_router import (
    BodyReferenceTypeError, load_registry, reference_context, resolve_types,
    validate_registry,
)
from tools.style_pack_manager import (
    StylePackError, command_startup_menu_template, load_body_reference_manifest,
    parse_aux_body_references,
)


class BodyReferenceRetirementTests(unittest.TestCase):
    def test_current_registry_retires_natural_and_sketch_only(self) -> None:
        registry = load_registry()
        self.assertFalse(registry["types"]["NATURAL"]["enabled"])
        self.assertFalse(registry["types"]["SKETCH"]["enabled"])
        self.assertNotIn("enabled", registry["types"]["OLCHAS"])
        self.assertEqual(set(registry["types"]), {"NATURAL", "SKETCH", "OLCHAS", "POSE_ATLAS"})
        self.assertEqual(registry["active_auxiliary_type"], "POSE_ATLAS")
        self.assertTrue(registry["types"]["POSE_ATLAS"]["enabled"])
        self.assertEqual(registry["types"]["POSE_ATLAS"]["status"], "ACTIVE_ALL_SOURCE_IMAGES")

    def test_resolution_reports_retired_alias_without_authority(self) -> None:
        registry = load_registry()
        result = resolve_types("используй натуру и OLCHAS", registry)
        self.assertEqual(result["selected_types"], ["OLCHAS"])
        self.assertEqual(result["disabled_requested_types"], ["NATURAL"])
        self.assertEqual(result["permanent_body_geometry_authority"], "APPROVED_CHARACTER_BODY_OR_COMPLETE_PROMPT_BODY_SPEC")

    def test_disabled_context_rejects_before_manifest_or_reference_access(self) -> None:
        registry = load_registry()
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(BodyReferenceTypeError, "disabled"):
                reference_context("SKETCH", {"target_anatomy": "FEMALE_ANATOMY"}, Path(folder), registry)

    def test_validate_skips_retired_files(self) -> None:
        registry = load_registry()
        registry["types"]["POSE_ATLAS"]["enabled"] = False
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            olchas = workspace / registry["types"]["OLCHAS"]["root"]
            olchas.mkdir(parents=True)
            (workspace / registry["types"]["OLCHAS"]["manifest"]).write_text("ref_id\n", encoding="utf-8")
            result = validate_registry(registry, workspace)
        rows = {item["type"]: item["status"] for item in result["types"]}
        self.assertEqual(rows["NATURAL"], "DISABLED")
        self.assertEqual(rows["SKETCH"], "DISABLED")
        self.assertEqual(rows["POSE_ATLAS"], "DISABLED")
        self.assertEqual(rows["OLCHAS"], "READY")

    def test_disabled_manifest_is_rejected_without_reading_library(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            config = workspace / "config"
            config.mkdir()
            config.joinpath("body_reference_types.json").write_text(
                json.dumps({"types": {"NATURAL": {"enabled": False, "retirement_reason": "retired"}}}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(StylePackError, "NATURAL.*disabled"):
                load_body_reference_manifest(workspace)

    def test_disabled_startup_suppresses_select_option_and_keeps_decline_mappings(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            config = workspace / "config"
            config.mkdir()
            registry = copy.deepcopy(load_registry())
            registry["active_auxiliary_type"] = "NATURAL"
            registry["types"]["NATURAL"].update({"enabled": False, "retirement_reason": "retired"})
            config.joinpath("body_reference_types.json").write_text(json.dumps(registry), encoding="utf-8")
            output = io.StringIO()
            args = Namespace(workspace=workspace, style_name="Test", character_id="CHAR_001", character_name="Name", generation_purpose="SCENE")
            with redirect_stdout(output):
                command_startup_menu_template(args)
            menu = json.loads(output.getvalue())
        self.assertEqual([option["id"] for option in menu["options"]], ["OPTION_2", "OPTION_3"])
        self.assertTrue(all(option["aux_body_decision"] == "DECLINED" for option in menu["options"]))
        self.assertIn("недоступен", menu["question"])

    def test_stale_selected_library_is_rejected_without_values_or_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            config = workspace / "config"
            config.mkdir()
            config.joinpath("body_reference_types.json").write_text(
                json.dumps({"types": {"NATURAL": {"enabled": False, "retirement_reason": "retired"}}}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(StylePackError, "NATURAL.*disabled"):
                parse_aux_body_references(workspace, [], "SELECTED", "", False, False)


if __name__ == "__main__":
    unittest.main()
