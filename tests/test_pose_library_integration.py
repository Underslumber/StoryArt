from argparse import Namespace
from contextlib import redirect_stdout
import copy
import csv
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from tools.body_reference_type_router import load_registry, reference_context
from tools.pose_reference_manager import import_atlas
from tools.style_pack_manager import (
    StylePackError, command_startup_menu_template, parse_aux_body_references,
)


class PoseLibraryIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name)
        self.library = self.workspace / "BODY_REFERENCE_LIBRARY" / "POSE_ATLAS"
        self.source_root = self.workspace / "source"
        self.source_root.mkdir()
        self.config = self.workspace / "config"
        self.config.mkdir()
        registry = copy.deepcopy(load_registry())
        registry["active_auxiliary_type"] = "POSE_ATLAS"
        registry["types"]["POSE_ATLAS"]["enabled"] = True
        (self.config / "body_reference_types.json").write_text(json.dumps(registry), encoding="utf-8")
        rows, reviews = [], []
        for index, label in enumerate(("FEMALE", "MALE"), start=1):
            source_id = f"SRC_{index:03d}"
            rel = f"{source_id}.img"
            data = f"fixture-image-{label}".encode()
            (self.source_root / rel).write_bytes(data)
            digest = hashlib.sha256(data).hexdigest()
            rows.append({"id": source_id, "system_ja": "立ち姿", "system_zh": "站姿", "pose_number": str(index),
                         "pose_name_ja": "立つ", "pose_name_zh": "站立", "category_ja": "基本", "category_zh": "基础",
                         "angle_ja": "正面", "angle_zh": "正面", "source_relative_path": rel, "sha256": digest})
            reviews.append({"source_id": source_id, "sha256": digest, "subject_label": label,
                            "visual_qa_status": "PASS", "safety_status": "SAFE",
                            "anatomy_reliability": "RELIABLE_FOR_POSE", "review_notes": "fixture review",
                            "review_evidence": "fixture evidence", "subject_label_evidence": "reviewed source metadata",
                            "pose_name_ru": "стойка", "category_ru": "основная", "pose_family": "STANDING"})
        index_path = self.workspace / "source-index.json"
        review_path = self.workspace / "reviews.json"
        index_path.write_text(json.dumps({"images": rows}, ensure_ascii=False), encoding="utf-8")
        review_path.write_text(json.dumps({"reviews": reviews}, ensure_ascii=False), encoding="utf-8")
        import_atlas(Namespace(source_index=index_path, source_root=self.source_root,
                               review_file=review_path, library_root=self.library))

    def tearDown(self):
        self.temp.cleanup()

    def target(self, anatomy):
        return {"target_anatomy": anatomy, "anatomy_evidence_source": "CURRENT_PROMPT_EXPLICIT:female"}

    def test_group_filter_uses_matching_subject_before_paths_and_keeps_group_metadata(self):
        registry = load_registry(self.config / "body_reference_types.json")
        female = reference_context("POSE_ATLAS", self.target("FEMALE_ANATOMY"), self.workspace, registry)
        male = reference_context("POSE_ATLAS", self.target("MALE_ANATOMY"), self.workspace, registry)
        self.assertEqual(female["view_count"], 1)
        self.assertEqual(male["view_count"], 1)
        fview = female["groups"][0]["views"][0]
        self.assertEqual(fview["source_id"], "SRC_001")
        self.assertEqual(fview["group_id"], female["groups"][0]["group_id"])
        self.assertNotEqual(fview["source_id"], male["groups"][0]["views"][0]["source_id"])

    def test_selected_pose_keeps_group_and_review_binding_and_rejects_body_build(self):
        with (self.library / "BODY_REFERENCE_MANIFEST.csv").open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        female = next(row for row in rows if row["subject_label"] == "FEMALE")
        target = self.target("FEMALE_ANATOMY")
        selected = parse_aux_body_references(self.workspace, [female["ref_id"] + "=STAGING_ONLY"],
                                             "SELECTED", "", False, False, target)
        self.assertEqual(selected[0]["group_id"], female["group_id"])
        self.assertEqual(selected[0]["source_id"], female["source_id"])
        self.assertEqual(selected[0]["angle"], "front")
        self.assertEqual(selected[0]["subject_label"], "FEMALE")
        self.assertEqual(selected[0]["review_evidence"], "fixture evidence")
        with self.assertRaisesRegex(StylePackError, "only be used as STAGING_ONLY"):
            parse_aux_body_references(self.workspace, [female["ref_id"] + "=BODY_BUILD_TARGET"],
                                      "SELECTED", "", False, True, target)

    def test_standard_menu_restores_option_one_only_for_enabled_pose_atlas(self):
        args = Namespace(workspace=self.workspace, style_name="Style", character_id="CHAR_001",
                         character_name="Character", generation_purpose="SCENE")
        output = io.StringIO()
        with redirect_stdout(output):
            command_startup_menu_template(args)
        menu = json.loads(output.getvalue())
        self.assertEqual([item["id"] for item in menu["options"]], ["OPTION_1", "OPTION_2", "OPTION_3"])
        self.assertEqual([(item["fidelity"], item["aux_body_decision"]) for item in menu["options"]],
                         [(90, "SELECTED"), (90, "DECLINED"), (70, "DECLINED")])
        self.assertIn("только для мягких референсов позы и ракурса камеры", menu["question"])

    def test_disabled_active_pose_atlas_does_not_fall_back_to_natural(self):
        registry_path = self.config / "body_reference_types.json"
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        registry["types"]["POSE_ATLAS"]["enabled"] = False
        registry["types"]["NATURAL"]["enabled"] = True
        registry_path.write_text(json.dumps(registry), encoding="utf-8")
        with self.assertRaisesRegex(StylePackError, "POSE_ATLAS body reference library is disabled"):
            parse_aux_body_references(self.workspace, [], "SELECTED", "", False, False,
                                      self.target("FEMALE_ANATOMY"))

    def test_retired_br_id_is_not_resolved_in_pose_atlas(self):
        with self.assertRaisesRegex(StylePackError, "Unknown body reference id"):
            parse_aux_body_references(self.workspace, ["BR_0007=STAGING_ONLY"],
                                      "SELECTED", "", False, False, self.target("FEMALE_ANATOMY"))


if __name__ == "__main__":
    unittest.main()
