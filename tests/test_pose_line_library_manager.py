from __future__ import annotations

import csv
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

from PIL import Image

from tools import pose_line_library_manager as manager


class PoseLineLibraryManagerTests(unittest.TestCase):
    def test_parse_search_results_preserves_source_and_query(self):
        page = (
            "&quot;alt&quot;:&quot;Female pose&quot;"
            " filler &quot;origUrl&quot;:&quot;https:\\/\\/img.example\\/pose.png&quot;"
            " filler &quot;snippet&quot;:{&quot;url&quot;:&quot;https:\\/\\/page.example\\/work&quot;}"
        )
        rows = manager.parse_search_results(page, "female pose line")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["original_url"], "https://img.example/pose.png")
        self.assertEqual(rows[0]["page_url"], "https://page.example/work")
        self.assertEqual(rows[0]["matched_queries"], ["female pose line"])

    def test_candidate_id_is_stable_and_url_specific(self):
        first = manager.stable_candidate_id("https://example.test/a.png")
        self.assertEqual(first, manager.stable_candidate_id("https://example.test/a.png"))
        self.assertNotEqual(first, manager.stable_candidate_id("https://example.test/b.png"))
        self.assertRegex(first, r"^PL_[A-F0-9]{12}$")

    def test_status_offers_collection_without_creating_library(self):
        with tempfile.TemporaryDirectory() as folder:
            items = manager.paths(Path(folder))
            payload = manager.status_payload(items)
            self.assertEqual(payload["status"], "NOT_BUILT")
            self.assertIn("CollectPoseLineLibrary", payload["collect_command"])
            self.assertFalse(items["root"].exists())

    def test_status_recognizes_existing_curated_anatomy_line_library(self):
        with tempfile.TemporaryDirectory() as folder:
            items = manager.paths(Path(folder))
            items["legacy_root"].mkdir(parents=True)
            manager.write_csv(
                items["legacy_final_manifest"],
                [{"candidate_id": "YX_1", "final_tier": "PRIMARY_LINE_ART"}],
                ("candidate_id", "final_tier"),
            )
            payload = manager.status_payload(items)
            self.assertEqual(payload["status"], "BELOW_TARGET")
            self.assertEqual(payload["source_layout"], "EXISTING_ANATOMY_CONTOUR_LIBRARY")
            self.assertEqual(payload["primary_count"], 1)

    def test_apply_review_requires_every_visual_decision(self):
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            items = manager.paths(workspace)
            manager.ensure_structure(items)
            source = items["incoming"] / "PL_TEST.png"
            Image.new("RGB", (300, 500), "white").save(source)
            digest = manager.sha256_file(source)
            row = {field: "" for field in manager.CANDIDATE_FIELDS}
            row.update(
                {
                    "candidate_id": "PL_TEST",
                    "qa_status": "PENDING_VISUAL_QA",
                    "local_path": str(source),
                    "sha256": digest,
                }
            )
            manager.write_csv(items["manifest"], [row], manager.CANDIDATE_FIELDS)
            manager.write_csv(items["review"], [{"candidate_id": "PL_TEST", "decision": "", "notes": ""}], ("candidate_id", "decision", "notes"))
            args = Namespace(workspace=str(workspace), review_file="", minimum_primary=1)
            with self.assertRaisesRegex(manager.PoseLineError, "Visual review is incomplete"):
                manager.command_apply_review(args)

    def test_apply_review_and_validate_preserve_hash_and_soft_role(self):
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            items = manager.paths(workspace)
            manager.ensure_structure(items)
            source = items["incoming"] / "PL_TEST.png"
            Image.new("RGB", (300, 500), "white").save(source)
            digest = manager.sha256_file(source)
            row = {field: "" for field in manager.CANDIDATE_FIELDS}
            row.update(
                {
                    "candidate_id": "PL_TEST",
                    "qa_status": "PENDING_VISUAL_QA",
                    "local_path": str(source),
                    "sha256": digest,
                }
            )
            manager.write_csv(items["manifest"], [row], manager.CANDIDATE_FIELDS)
            manager.write_csv(
                items["review"],
                [{"candidate_id": "PL_TEST", "decision": "PRIMARY_POSE_LINE", "notes": "clean pose"}],
                ("candidate_id", "decision", "notes"),
            )
            apply_args = Namespace(workspace=str(workspace), review_file="", minimum_primary=1)
            self.assertEqual(manager.command_apply_review(apply_args), 0)
            final_rows = manager.read_csv(items["final_manifest"])
            self.assertEqual(final_rows[0]["allowed_role"], "POSE_SOFT")
            self.assertIn("BODY_BUILD_TARGET", final_rows[0]["forbidden_roles"])
            self.assertEqual(manager.sha256_file(Path(final_rows[0]["curated_path"])), digest)
            validate_args = Namespace(workspace=str(workspace), minimum_primary=1)
            self.assertEqual(manager.command_validate(validate_args), 0)

    def test_gitignore_keeps_local_library_out_of_repository(self):
        root = Path(__file__).resolve().parents[1]
        ignore = (root / ".gitignore").read_text(encoding="utf-8")
        bootstrap = (root / "scripts" / "bootstrap.ps1").read_text(encoding="utf-8")
        self.assertIn("/*", ignore)
        self.assertIn("*.png", ignore)
        self.assertIn("POSE_LINE_LIBRARY_OFFER", bootstrap)
        self.assertIn("CollectPoseLineLibrary", bootstrap)


if __name__ == "__main__":
    unittest.main()
