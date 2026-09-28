"""Focused regression tests for the preparation performance."""

from __future__ import annotations

import csv
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


from tools import style_pack_manager as manager


class PreparationPerformanceTests(unittest.TestCase):
    def test_latest_rejection_across_copies_and_lookup_local_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            target = root / "target.png"
            approved_copy = root / "approved.png"
            rejected_copy = root / "rejected.png"
            for path in (target, approved_copy, rejected_copy):
                path.write_bytes(b"identical-image-bytes")
            paths = manager.make_paths(workspace, "Known Style")
            paths.generation_manifest.parent.mkdir(parents=True)
            with paths.generation_manifest.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=["status", "source_image", "archive_file", "style_file"])
                writer.writeheader()
                writer.writerow({"status": "APPROVED", "style_file": str(approved_copy)})
                writer.writerow({"status": "REJECTED", "style_file": str(rejected_copy)})
            original_hash = manager.sha256
            reads: list[Path] = []

            def counted(path: Path) -> str:
                reads.append(Path(path))
                return original_hash(path)

            with patch.object(manager, "sha256", side_effect=counted):
                row = manager.newest_matching_generation(paths, target)
            self.assertEqual(row["status"], "REJECTED")
            self.assertEqual(reads.count(target), 1)
            self.assertLessEqual(reads.count(approved_copy), 1)
            self.assertLessEqual(reads.count(rejected_copy), 1)

            target.write_bytes(b"changed-image-bytes!!")
            with patch.object(manager, "sha256", wraps=original_hash) as hashed:
                self.assertIsNone(manager.newest_matching_generation(paths, target))
                self.assertEqual(hashed.call_count, 3)  # new target digest plus each current-size copy

    def test_target_path_is_hashed_once_and_directory_manifest_paths_are_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            target = root / "target.bin"
            target.write_bytes(b"")
            directory_candidate = root / "not-a-file"
            directory_candidate.mkdir()
            paths = manager.make_paths(workspace, "Known Style")
            paths.generation_manifest.parent.mkdir(parents=True)
            with paths.generation_manifest.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=["status", "source_image", "archive_file", "style_file"])
                writer.writeheader()
                writer.writerow({"status": "APPROVED", "source_image": str(target), "archive_file": str(target), "style_file": str(target)})
                writer.writerow({"status": "REJECTED", "style_file": str(directory_candidate)})
            original_hash = manager.sha256
            hashed_paths: list[Path] = []

            def counted(path: Path) -> str:
                hashed_paths.append(Path(path))
                return original_hash(path)

            with patch.object(manager, "sha256", side_effect=counted):
                row = manager.newest_matching_generation(paths, target)
            self.assertEqual(row["status"], "APPROVED")
            self.assertEqual(hashed_paths.count(target), 1)
            self.assertNotIn(directory_candidate, hashed_paths)

    def test_style_summary_skips_image_decoding_without_changing_counts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pack = Path(directory) / "known_PROJECT_PACK"
            image = pack / "01_WORK" / "FACE_CROPS" / "face.png"
            image.parent.mkdir(parents=True)
            image.write_bytes(b"placeholder")
            paths = types.SimpleNamespace(workspace=pack.parent, pack=pack, style_name="known")
            discovered = manager.DiscoveredStyle(
                style_name="known", slug="known", pack_path=str(pack), generations_path="",
                management="LEGACY", local_readiness="READY", web_readiness="EMPTY",
                source_images=0, work_images=1, upload_images=0, upload_documents=0,
                characters=0, can_generate=True, can_create_web_project=False, notes="",
            )
            with patch.object(manager, "matching_discovered_style", return_value=discovered), \
                 patch.object(manager, "image_info", side_effect=AssertionError("decode should be skipped")):
                summary = manager.build_style_context(paths, "ALL", False, False)
            with patch.object(manager, "matching_discovered_style", return_value=discovered), \
                 patch.object(manager, "image_info", return_value=("PNG", 1, 1)):
                detailed = manager.build_style_context(paths, "ALL", False, True)
            self.assertEqual(summary["local_files_total"], 1)
            self.assertEqual(summary["matching_files"], 1)
            self.assertEqual(summary["file_type_counts"], {"IMAGE": 1})
            self.assertNotIn("files", summary)
            self.assertEqual({key: value for key, value in detailed.items() if key != "files"}, summary)

    def test_style_lookup_passes_only_the_known_pack_to_discovery(self) -> None:
        workspace = Path("D:/workspace")
        pack = workspace / "known_PROJECT_PACK"
        paths = types.SimpleNamespace(workspace=workspace, pack=pack)
        discovered = types.SimpleNamespace(pack_path=str(pack))
        with patch.object(manager, "discover_style_packs", return_value=[discovered]) as discover:
            self.assertIs(manager.matching_discovered_style(paths), discovered)
        discover.assert_called_once_with(workspace, pack)

    def test_prepare_context_uses_manifest_metadata_without_inventory_walk(self) -> None:
        pack = Path("D:/workspace/known_PROJECT_PACK")
        paths = types.SimpleNamespace(style_name="known", pack=pack)
        discovered = types.SimpleNamespace(
            style_name="known", pack_path=str(pack), local_readiness="READY",
        )
        with patch.object(manager, "matching_discovered_style", return_value=discovered), \
             patch.object(manager, "manifest_role_files", return_value=["anchor.png"]) as manifest, \
             patch.object(manager, "active_style_calibration_summary", return_value={"status": "NOT_APPLIED"}), \
             patch.object(manager, "build_style_context", side_effect=AssertionError("inventory is explicit-only")), \
             patch.object(manager.Path, "rglob", side_effect=AssertionError("preparation must not walk the pack")):
            context = manager._minimal_preparation_style_context(paths)
        self.assertEqual(context["style_name"], "known")
        self.assertEqual(context["explicit_anchor_files"], ["anchor.png"])
        self.assertNotIn("review_pool_counts", context)
        manifest.assert_called_once_with(pack, "ANCHOR_STYLE")

    def test_reused_resolution_rejects_a_source_changed_after_risk_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.png"
            source.write_bytes(b"before")
            paths = types.SimpleNamespace()
            plan_path = root / "REFERENCE_PLAN.json"
            plan = {
                "request_id": "req", "character_id": "NONE", "generation_workflow": {"mode": "SINGLE_PASS"},
            }
            snapshot = [{
                "path": str(source), "sha256": manager.sha256(source), "active_roles": ["STYLE"],
                "slot": "style", "physically_attach": True,
            }]
            source.write_bytes(b"after!")
            with patch.object(manager, "validated_stage_outputs", return_value={}), \
                 patch.object(manager, "assert_profile_plan_current"), \
                 patch.object(manager, "validate_resolved_slot_compatibility"):
                with self.assertRaisesRegex(manager.StylePackError, "missing or changed"):
                    manager.resolve_execution_call(
                        paths, plan_path, plan, "SINGLE_PASS", "prompt", "risk.json",
                        resolved_slots=snapshot, resolved_stage_outputs={},
                    )

    def test_reused_resolution_still_rejects_profile_and_stage_output_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.png"
            source.write_bytes(b"current")
            paths = types.SimpleNamespace()
            plan_path = root / "REFERENCE_PLAN.json"
            plan = {"request_id": "req", "character_id": "CHAR_001", "generation_workflow": {"mode": "SINGLE_PASS"}}
            slots = [{"path": str(source), "sha256": manager.sha256(source), "active_roles": ["FACE"], "slot": "face"}]
            with patch.object(manager, "validated_stage_outputs", return_value={}), \
                 patch.object(manager, "assert_profile_plan_current", side_effect=manager.StylePackError("confirmed profile changed")), \
                 patch.object(manager, "resolve_call_slots") as resolve_slots:
                with self.assertRaisesRegex(manager.StylePackError, "confirmed profile changed"):
                    manager.resolve_execution_call(
                        paths, plan_path, plan, "SINGLE_PASS", "prompt", "risk.json",
                        resolved_slots=slots, resolved_stage_outputs={},
                    )
                resolve_slots.assert_not_called()

            previous = {"STAGE_1": {"path": str(source), "sha256": "a" * 64}}
            changed = {"STAGE_1": {"path": str(source), "sha256": "b" * 64}}
            with patch.object(manager, "validated_stage_outputs", return_value=changed), \
                 patch.object(manager, "resolve_call_slots") as resolve_slots:
                with self.assertRaisesRegex(manager.StylePackError, "stage outputs changed"):
                    manager.resolve_execution_call(
                        paths, plan_path, plan, "SINGLE_PASS", "prompt", "risk.json",
                        resolved_slots=slots, resolved_stage_outputs=previous,
                    )
                resolve_slots.assert_not_called()

    def test_resolve_execution_call_validates_stage_outputs_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = types.SimpleNamespace()
            plan_path = root / "REFERENCE_PLAN.json"
            output = {"path": str(root / "stage.png"), "sha256": "a" * 64, "stage_id": "ONE"}
            plan = {
                "request_id": "req", "character_id": "NEW",
                "generation_workflow": {"mode": "MULTI_STAGE", "stages": [
                    {"stage_id": "TWO", "slots": [{"path": "<STAGE_OUTPUT:ONE>"}]}
                ]},
            }
            seen: list[object] = []

            def resolve_slots(_paths, _plan_path, _plan, _stage, stage_outputs=None):
                seen.append(stage_outputs)
                return []

            with patch.object(manager, "validated_stage_outputs", return_value={"ONE": output}) as validated, \
                 patch.object(manager, "resolve_call_slots", side_effect=resolve_slots), \
                 patch.object(manager, "load_and_validate_risk_assessment", return_value=(root / "risk.json", {"generation_risk": "D1"})):
                call, _, _ = manager.resolve_execution_call(paths, plan_path, plan, "TWO", "prompt", "risk.json")
            self.assertEqual(validated.call_count, 1)
            self.assertIs(seen[0], validated.return_value)
            self.assertEqual(call["stage_output_bindings"], [output])


if __name__ == "__main__":
    unittest.main()
