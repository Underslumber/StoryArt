from __future__ import annotations

from argparse import Namespace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools import style_pack_manager as manager


class StyleReferenceScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        work_dir = Path(__file__).resolve().parents[1] / "work"
        work_dir.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=work_dir)
        self.addCleanup(self.temp.cleanup)
        self.paths = manager.make_paths(Path(self.temp.name), "TEST_STYLE")
        manager.command_init(Namespace(
            workspace=Path(self.temp.name), style_name="TEST_STYLE", source="", non_recursive=False,
        ))
        manager.write_reference_manifests(self.paths, [])

    def _asset(self, relative: str, data: bytes = b"image fixture") -> Path:
        path = self.paths.pack / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def _row(self, relative: str, **overrides: str) -> dict[str, str]:
        defaults = {
            "reference_id": "REF_00001",
            "filename": Path(relative).name,
            "stored_relative_path": relative,
            "primary_role": "MASTER_STYLE",
            "status": "TEST",
            "generator_safe": "YES",
            "character_id": "",
            "reference_scope": "",
            "request_id": "",
            "source_sha256": "",
            "notes": "",
            "use_for": "",
        }
        return {**defaults, **overrides}

    def test_legacy_beal_like_assets_resolve_nonstyle_and_general_face_core_stays_style(self) -> None:
        rows = [
            self._row("01_WORK/FACE_CROPS/CHARACTER_FACE_BEAL_CANON_01_BOOK.png", character_id="TEMP_Beal"),
            self._row("01_WORK/FACE_CROPS/CHARACTER_FACE_BEAL_CANON_02_MAGIC.png", character_id="TEMP_Beal"),
            self._row("01_WORK/FACE_CROPS/FACE_EXPRESSION_BEAL_CANON_03_SCENE.png", character_id="TEMP_Beal"),
            self._row("01_WORK/BODY_CROPS/CHARACTER_BODY_BEAL_CANON_02_MAGIC.png", character_id="TEMP_Beal"),
            self._row("01_WORK/FACE_CROPS/FACE_CROP_BEAL.png", character_id="TEMP_Beal"),
            self._row("01_WORK/FACE_CROPS/NEW_LORE_FACE.png", character_id="NEW", primary_role="CHARACTER_FACE"),
            self._row("01_WORK/STYLE_CROPS/MASTER_STYLE_BOUND.png", character_id="CHAR_002"),
            self._row("01_WORK/FACE_CROPS/FACE_CORE_STYLE.png", primary_role="FACE_CORE"),
            self._row("01_WORK/STYLE_CROPS/STYLE_FACE_EXPRESSION_GENERAL.png", primary_role="FACE_EXPRESSION"),
            self._row("01_WORK/STYLE_CROPS/STYLE_FACE_CROP_GENERAL.png", primary_role="FACE_CROP"),
            self._row("01_WORK/STYLE_CROPS/MASTER_STYLE_REQUEST.png", character_id="TEMP_Beal", request_id="REQ_BEAL", notes="request-local rendering support, not permanent anchor"),
        ]
        scopes = [manager.effective_reference_scope(row) for row in rows]
        self.assertEqual(["CHARACTER"] * 7 + ["STYLE"] * 3 + ["REQUEST"], scopes)

    def test_style_context_excludes_bound_rows_but_keeps_general_expression_and_crop(self) -> None:
        bound = self._asset("01_WORK/STYLE_CROPS/MASTER_STYLE_CHARACTER.png", b"bound character image")
        expression = self._asset("01_WORK/STYLE_CROPS/STYLE_FACE_EXPRESSION_GENERAL.png", b"general expression image")
        crop = self._asset("01_WORK/STYLE_CROPS/STYLE_FACE_CROP_GENERAL.png", b"general crop image")
        manager.write_reference_manifests(self.paths, [
            self._row(bound.relative_to(self.paths.pack).as_posix(), character_id="CHAR_002"),
            self._row(expression.relative_to(self.paths.pack).as_posix(), primary_role="FACE_EXPRESSION"),
            self._row(crop.relative_to(self.paths.pack).as_posix(), primary_role="FACE_CROP"),
        ])
        context = manager.build_style_context(self.paths, "STYLE", False, False)
        self.assertEqual(2, context["work_collection_counts"]["STYLE_CROPS"])

    def test_master_manifest_and_positive_gate_exclude_bound_character_and_request_rows(self) -> None:
        bound = self._asset("01_WORK/STYLE_CROPS/MASTER_STYLE_BOUND.png")
        request = self._asset("01_WORK/STYLE_CROPS/MASTER_STYLE_REQUEST.png")
        style = self._asset("01_WORK/STYLE_CROPS/MASTER_STYLE_OK.png")
        rows = [
            self._row(bound.relative_to(self.paths.pack).as_posix(), character_id="CHAR_002"),
            self._row(request.relative_to(self.paths.pack).as_posix(), reference_scope="REQUEST", request_id="REQ_2"),
            self._row(style.relative_to(self.paths.pack).as_posix(), reference_scope="STYLE"),
        ]
        manager.write_reference_manifests(self.paths, rows)
        self.assertEqual([style.name], manager.manifest_role_files(self.paths.pack, "MASTER_STYLE"))
        self.assertFalse(manager.has_positive_master_style_manifest_entry(self.paths, bound.relative_to(self.paths.pack)))
        self.assertFalse(manager.has_positive_master_style_manifest_entry(self.paths, request.relative_to(self.paths.pack)))
        self.assertTrue(manager.has_positive_master_style_manifest_entry(self.paths, style.relative_to(self.paths.pack)))
        self.assertTrue(manager.has_positive_master_style_manifest_entry(self.paths, request.relative_to(self.paths.pack), "REQ_2"))
        self.assertFalse(manager.has_positive_master_style_manifest_entry(self.paths, request.relative_to(self.paths.pack), "REQ_OTHER"))
        with self.assertRaisesRegex(manager.StylePackError, "scoped reference"):
            manager.validate_plan_reference(self.paths, str(request), "STYLE", allow_request_local_style_candidate=True, request_id="REQ_OTHER")
        current_request = manager.validate_plan_reference(
            self.paths, str(request), "STYLE", allow_request_local_style_candidate=True, request_id="REQ_2",
        )
        self.assertEqual("REQUEST_LOCAL_STYLE_CANDIDATE", current_request["status"])

    def test_classify_rejects_copy_or_rename_of_registered_character_source_as_style(self) -> None:
        source = self._asset("00_SOURCE_ORIGINALS/renamed-face.png")
        manager.write_reference_manifests(self.paths, [self._row(
            "00_SOURCE_ORIGINALS/renamed-face.png", filename=source.name,
            primary_role="CHARACTER_FACE", character_id="TEMP_Beal", reference_scope="CHARACTER",
        )])
        args = Namespace(
            workspace=Path(self.temp.name), style_name="TEST_STYLE", role="MASTER_STYLE", status="TEST",
            user_approved=False, subject_label="UNKNOWN", subject_label_evidence="", file=str(source),
            source_reference="", crop_box="", reference_scope="STYLE", request_id="", character_id="",
            anatomy_compatibility="UNKNOWN", anatomy_evidence_source="UNKNOWN", secondary_role=[],
            shot_type="", expression="", lighting="", background_type="", text_present="UNKNOWN",
            generator_safe=True, use_for="Assigned role only.", do_not_use_for="", notes="",
        )
        with self.assertRaisesRegex(manager.StylePackError, "Cannot promote"):
            manager.command_classify(args)

        request_source = self._asset("incoming/request-rendering-crop.png", b"request-specific support")
        args.file = str(request_source)
        args.reference_scope = "REQUEST"
        args.request_id = "REQ_CURRENT"
        manager.command_classify(args)
        classified = manager.read_csv(self.paths.references)[-1]
        self.assertEqual("REQUEST", classified["reference_scope"])
        self.assertEqual("REQ_CURRENT", classified["request_id"])

    def test_ingest_infers_existing_exact_character_binding_and_accepts_explicit_character_id(self) -> None:
        from base64 import b64decode

        png = b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/S2cAAAAASUVORK5CYII=")
        bound = self._asset("00_SOURCE_ORIGINALS/registered-face.png", png)
        manager.write_reference_manifests(self.paths, [self._row(
            bound.relative_to(self.paths.pack).as_posix(), primary_role="CHARACTER_FACE",
            character_id="TEMP_Beal", reference_scope="CHARACTER",
        )])
        source = Path(self.temp.name) / "incoming"
        source.mkdir()
        (source / "renamed.png").write_bytes(png)
        manager.ingest_source(self.paths, source)
        rows = manager.read_csv(self.paths.references)
        imported = next(row for row in rows if row.get("source_filename") == "renamed.png")
        self.assertEqual("CHARACTER", imported["reference_scope"])
        self.assertEqual("TEMP_Beal", imported["character_id"])

        second = Path(self.temp.name) / "incoming-character"
        second.mkdir()
        (second / "neutral.png").write_bytes(png + b"new")
        manager.ingest_source(self.paths, second, character_id="NEW")
        imported_new = next(row for row in manager.read_csv(self.paths.references) if row.get("source_filename") == "neutral.png")
        self.assertEqual("CHARACTER", imported_new["reference_scope"])
        self.assertEqual("NEW", imported_new["character_id"])

    def test_finalize_projects_only_style_rows_without_deleting_scoped_files(self) -> None:
        style = self._asset("01_WORK/STYLE_CROPS/MASTER_STYLE_OK.png")
        character = self._asset("01_WORK/STYLE_CROPS/MASTER_STYLE_CHARACTER.png")
        request = self._asset("01_WORK/STYLE_CROPS/MASTER_STYLE_REQUEST.png")
        rows = [
            self._row(style.relative_to(self.paths.pack).as_posix(), reference_scope="STYLE", status="APPROVED", user_approved="YES"),
            self._row(character.relative_to(self.paths.pack).as_posix(), character_id="CHAR_001", status="APPROVED", user_approved="YES"),
            self._row(request.relative_to(self.paths.pack).as_posix(), request_id="REQ_1", status="APPROVED", user_approved="YES"),
        ]
        manager.write_reference_manifests(self.paths, rows)
        manager.command_finalize(Namespace(workspace=Path(self.temp.name), style_name="TEST_STYLE", user_approved=True))
        uploaded = manager.read_csv(self.paths.upload_manifest)
        self.assertEqual([style.name], [row["filename"] for row in uploaded])
        self.assertTrue(character.is_file())
        self.assertTrue(request.is_file())

    def test_ordinary_role_classification_inherits_character_and_request_scope(self) -> None:
        for scope, request_id in (("CHARACTER", ""), ("REQUEST", "REQ_ORIGINAL")):
            with self.subTest(scope=scope):
                source = self._asset(f"incoming/neutral-{scope}.png", scope.encode())
                row = self._row(source.relative_to(self.paths.pack).as_posix(), primary_role="FACE_CORE",
                                reference_scope=scope, character_id="CHAR_42", request_id=request_id)
                manager.write_reference_manifests(self.paths, [row])
                args = manager.build_parser().parse_args([
                    "classify", "--workspace", self.temp.name, "--style-name", "TEST_STYLE",
                    "--file", str(source), "--role", "FACE_EXPRESSION", "--status", "APPROVED",
                    "--user-approved", "--generator-safe",
                ])
                manager.command_classify(args)
                classified = manager.read_csv(self.paths.references)[-1]
                self.assertEqual(scope, classified["reference_scope"])
                self.assertEqual(request_id, classified["request_id"])
                self.assertEqual("CHAR_42", classified["character_id"])
                self.assertEqual([], manager.read_csv(self.paths.style_references))
                with self.assertRaisesRegex(manager.StylePackError, "No .*STYLE references"):
                    manager.command_finalize(Namespace(workspace=Path(self.temp.name), style_name="TEST_STYLE", user_approved=True))

    def test_scoped_original_provenance_is_preserved_without_tainting_shared_style_parent(self) -> None:
        original = self._asset("incoming/neutral-original.png", b"original pixels")
        crop = self._asset("01_WORK/FACE_CROPS/character-crop.png", b"crop pixels")
        scoped = self._row(crop.relative_to(self.paths.pack).as_posix(), primary_role="CHARACTER_FACE",
                           character_id="CHAR_7", source_sha256=manager.sha256(original))
        manager.write_reference_manifests(self.paths, [scoped])
        self.assertTrue(manager.is_nonstyle_style_asset(self.paths, original))
        self.assertEqual("CHAR_7", manager.reference_binding_for_asset(self.paths, original)["character_id"])
        general = self._row("01_WORK/STYLE_CROPS/MASTER_STYLE_general.png", primary_role="MASTER_STYLE",
                            status="APPROVED", generator_safe="YES", source_sha256=manager.sha256(original))
        manager.write_reference_manifests(self.paths, [scoped, general])
        self.assertFalse(manager.is_nonstyle_style_asset(self.paths, original))
        self.assertIsNone(manager.reference_binding_for_asset(self.paths, original))
        self.assertTrue(manager.is_nonstyle_style_asset(self.paths, crop))

    def test_classify_rejects_conflicting_approved_asset_owners_before_writing(self) -> None:
        source = self._asset("incoming/neutral-a.png", b"pixels A")
        provenance = self._asset("incoming/neutral-b.png", b"pixels B")
        args = manager.build_parser().parse_args([
            "classify", "--workspace", self.temp.name, "--style-name", "TEST_STYLE",
            "--file", str(source), "--source-reference", str(provenance), "--role", "FACE_CORE",
        ])
        def asset(_, file):
            return {"character_id": "CHAR_A" if file == source.resolve() else "CHAR_B"}
        before = self.paths.references.read_bytes()
        with patch.object(manager, "registered_approved_character_asset", side_effect=asset):
            with self.assertRaisesRegex(manager.StylePackError, "different characters"):
                manager.command_classify(args)
        self.assertEqual(before, self.paths.references.read_bytes())
        self.assertFalse((self.paths.pack / "01_WORK/FACE_CROPS/FACE_CORE_neutral-a.png").exists())

    def test_identical_scoped_copy_does_not_capture_shared_style_original(self) -> None:
        original = self._asset("00_SOURCE_ORIGINALS/shared.png", b"same pixels")
        first = self._asset("01_WORK/POSE_CROPS/request-a.png", b"same pixels")
        second = self._asset("01_WORK/POSE_CROPS/request-b.png", b"same pixels")
        style = self._row(original.relative_to(self.paths.pack).as_posix(), status="APPROVED",
                          source_sha256=manager.sha256(original))
        scoped_a = self._row(first.relative_to(self.paths.pack).as_posix(), request_id="REQ_A")
        scoped_b = self._row(second.relative_to(self.paths.pack).as_posix(), request_id="REQ_B")
        for order in ([scoped_a, scoped_b, style], [scoped_b, style, scoped_a]):
            manager.write_reference_manifests(self.paths, order)
            self.assertFalse(manager.is_nonstyle_style_asset(self.paths, original))
            self.assertIsNone(manager.reference_binding_for_asset(self.paths, original))
            self.assertTrue(manager.is_nonstyle_style_asset(self.paths, second))
            self.assertEqual("REQ_B", manager.reference_binding_for_asset(self.paths, second)["request_id"])
        # Without a general style origin, exact-path identity still wins over byte aliases.
        manager.write_reference_manifests(self.paths, [scoped_a, scoped_b])
        self.assertEqual("REQ_B", manager.reference_binding_for_asset(self.paths, second)["request_id"])

    def test_projection_has_explicit_scope_and_validate_accepts_legacy_upload_schema(self) -> None:
        row = self._row("01_WORK/FACE_CROPS/general.png", primary_role="FACE_CORE")
        file = self._asset(row["stored_relative_path"])
        manager.write_reference_manifests(self.paths, [row])
        self.assertEqual("", manager.read_csv(self.paths.references)[0]["reference_scope"])
        self.assertEqual("STYLE", manager.read_csv(self.paths.style_references)[0]["reference_scope"])
        legacy = [field for field in manager.REFERENCE_FIELDS if field not in {"reference_scope", "request_id"}]
        manager.write_csv(self.paths.upload_manifest, legacy, [])
        args = Namespace(workspace=Path(self.temp.name), style_name="TEST_STYLE", strict=False)
        manager.command_validate(args)
        manager.write_csv(self.paths.inventory, tuple(manager.INVENTORY_FIELDS) + ("unexpected",), [])
        with self.assertRaises(manager.StylePackError):
            manager.command_validate(args)

    def test_repair_is_idempotent_non_destructive_and_preserves_extra_columns(self) -> None:
        face = self._asset("01_WORK/FACE_CROPS/CHARACTER_FACE_BEAL_CANON_01_BOOK.png")
        style = self._asset("01_WORK/STYLE_CROPS/MASTER_STYLE_GENERAL.png")
        rows = [
            {**self._row(face.relative_to(self.paths.pack).as_posix(), character_id="TEMP_Beal"), "legacy_note": "keep-me"},
            {**self._row(style.relative_to(self.paths.pack).as_posix()), "legacy_note": "also-keep"},
        ]
        manager.write_csv(self.paths.references, tuple(manager.REFERENCE_FIELDS) + ("legacy_note",), rows)
        preview = Namespace(workspace=Path(self.temp.name), style_name="TEST_STYLE", apply=False, json=True)
        manager.command_repair_reference_scopes(preview)
        self.assertEqual("", manager.read_csv(self.paths.references)[0]["reference_scope"])
        apply = Namespace(workspace=Path(self.temp.name), style_name="TEST_STYLE", apply=True, json=True)
        manager.command_repair_reference_scopes(apply)
        repaired = manager.read_csv(self.paths.references)
        self.assertEqual(["CHARACTER", "STYLE"], [row["reference_scope"] for row in repaired])
        self.assertEqual(["keep-me", "also-keep"], [row["legacy_note"] for row in repaired])
        self.assertEqual(1, len(manager.read_csv(self.paths.style_references)))
        manager.command_repair_reference_scopes(apply)
        self.assertEqual(["CHARACTER", "STYLE"], [row["reference_scope"] for row in manager.read_csv(self.paths.references)])
        self.assertTrue(face.is_file())
        self.assertTrue(style.is_file())


if __name__ == "__main__":
    unittest.main()
