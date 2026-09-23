"""Public CLI workflow integration using synthetic images and no generator calls."""

from __future__ import annotations

import hashlib
import json
import csv
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]


class GenerationWorkflowIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Path(self.temporary.name)
        self.style_name = "INTEGRATION"
        self.style_pack = self.workspace / f"{self.style_name}_PROJECT_PACK"
        self.generations = self.workspace / f"{self.style_name}_GENERATIONS"
        self._cli("style_pack_manager.py", "init", "--workspace", self.workspace, "--style-name", self.style_name)
        metadata_path = self.style_pack / ".style-pack.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["status"] = "FINALIZED_APPROVED"
        metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        self.style_reference = self._classify("style.png", "ANCHOR_STYLE", (24, 28, 36))
        self.primary_face = self._classify("primary-face.png", "CHARACTER_FACE", (175, 115, 105))
        self.supporting_face = self._classify("supporting-face.png", "CHARACTER_FACE", (165, 108, 98))

    def _run(self, script: str, *args: object, succeeds: bool = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [sys.executable, "-B", str(ROOT / "tools" / script), *(str(arg) for arg in args)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        if succeeds:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def _cli(self, script: str, *args: object, succeeds: bool = True):
        return self._run(script, *args, succeeds=succeeds)

    def _classify(self, filename: str, role: str, color: tuple[int, int, int]) -> Path:
        source = self.workspace / filename
        Image.new("RGB", (96, 128), color).save(source)
        result = self._cli(
            "style_pack_manager.py", "classify", "--workspace", self.workspace,
            "--style-name", self.style_name, "--file", source, "--role", role,
            "--status", "ANCHOR" if role == "ANCHOR_STYLE" else "TEST",
            *( ("--user-approved",) if role == "ANCHOR_STYLE" else () ),
        )
        stored = next(line.removeprefix("FILE=") for line in result.stdout.splitlines() if line.startswith("FILE="))
        return Path(stored)

    def _guard_path(self, request_id: str) -> Path:
        return self.generations / "00_PENDING" / request_id / "EXECUTION_GUARD.json"

    def test_approved_canonical_character_assets_are_positive_references(self):
        from tools import style_pack_manager as manager

        paths = manager.make_paths(self.workspace, self.style_name)
        folder = paths.generations / "01_APPROVED_CHARACTERS" / "CHAR_001_Chance"
        base_dir = folder / "01_APPROVED_BASE"
        face_dir = folder / "03_FACE_REFERENCES"
        body_dir = folder / "04_BODY_REFERENCES"
        for directory in (base_dir, face_dir, body_dir):
            directory.mkdir(parents=True)
        base, face, body = (base_dir / "CHARACTER_ASSEMBLY_USER_SELECTED.png", face_dir / "face.png", body_dir / "body.png")
        unlisted = folder / "unlisted.png"
        for index, image in enumerate((base, face, body, unlisted)):
            Image.new("RGB", (32, 48), (80 + index, 100, 120)).save(image)
        profile = folder / "CHARACTER_PROFILE.yaml"
        profile.write_text(
            "character_id: CHAR_001\nname: Chance\nstatus: APPROVED\n"
            "identity:\n  gender_identity: man\n  visible_presentation: masculine\n"
            "anatomy_compatibility:\n  target_anatomy: MALE_ANATOMY\n  evidence_source: APPROVED_PROFILE\n"
            'character_face_references:\n  - "03_FACE_REFERENCES/face.png"\n'
            'character_body_references:\n  - "04_BODY_REFERENCES/body.png"\n',
            encoding="utf-8",
        )
        manager.write_csv(paths.character_registry, manager.CHARACTER_FIELDS, [{
            "character_id": "CHAR_001", "name": "Chance", "approved_base": str(base),
            "profile_path": str(profile), "face_references": str(face), "body_references": str(body),
            "status": "APPROVED",
        }])
        manager.append_generation(
            paths, request_id="canonical-character", character_id="CHAR_001",
            status="APPROVED_CHARACTER_BASE", fidelity=70, risk_level="D1",
            description="Approved canonical character base", source_image=base,
            archive_file=base, style_file=base,
        )

        self.assertEqual(manager.validate_plan_reference(paths, str(base), "FACE")["status"], "APPROVED_CHARACTER_ASSET")
        self.assertEqual(manager.validate_plan_reference(paths, str(face), "FACE")["status"], "APPROVED_CHARACTER_ASSET")
        self.assertEqual(manager.validate_plan_reference(paths, str(body), "BODY")["status"], "APPROVED_CHARACTER_ASSET")
        target = manager.compatibility_target(paths, type("Args", (), {"character_id": "CHAR_001"})())
        self.assertEqual(target["target_anatomy"], "MALE_ANATOMY")
        face_metadata = manager.style_reference_compatibility_metadata(paths, face)
        body_metadata = manager.style_reference_compatibility_metadata(paths, body)
        assembly_metadata = manager.style_reference_compatibility_metadata(paths, base)
        self.assertTrue(manager.enforce_reference_compatibility(target, face_metadata, ["FACE"])["compatible"])
        self.assertTrue(manager.enforce_reference_compatibility(target, body_metadata, ["BODY_BUILD"])["compatible"])
        self.assertTrue(manager.enforce_reference_compatibility(target, assembly_metadata, ["BODY_BUILD"])["compatible"])

        def rejected(candidate: Path):
            with self.assertRaises(manager.StylePackError):
                manager.validate_plan_reference(paths, str(candidate), "FACE")

        rejected(unlisted)
        rejected(self.generations / "ordinary-unregistered.png")

        original_registry = manager.read_csv(paths.character_registry)[0]
        row = dict(original_registry, status="REVIEW")
        manager.write_csv(paths.character_registry, manager.CHARACTER_FIELDS, [row])
        rejected(base)
        manager.write_csv(paths.character_registry, manager.CHARACTER_FIELDS, [original_registry])

        profile.write_text(profile.read_text(encoding="utf-8").replace("status: APPROVED", "status: REVIEW"), encoding="utf-8")
        rejected(base)
        profile.write_text(profile.read_text(encoding="utf-8").replace("status: REVIEW", "status: APPROVED"), encoding="utf-8")

        manifest_rows = manager.read_csv(paths.generation_manifest)
        manifest_rows[-1]["style_file"] = str(face)
        manager.write_csv(paths.generation_manifest, manager.GENERATION_FIELDS, manifest_rows)
        rejected(base)

    def _start_request(self, request_id: str, *, required_stages: tuple[str, ...] = ()) -> Path:
        guard_path = self._guard_path(request_id)
        args: list[object] = [
            "start", "--state", guard_path, "--request-id", request_id,
            "--goal", "Run a synthetic StoryArt workflow integration fixture.",
            "--deliverable", "One synthetic image result.",
        ]
        for stage in required_stages:
            args.extend(("--required-stage", stage))
        self._cli("task_execution_guard.py", *args)
        return guard_path

    def _prepare_scene(
        self,
        request_id: str,
        fidelity: int = 70,
        *,
        start_guard: bool = True,
        startup_mode: str = "DIRECT_CONFIRMATION",
        startup_quote: str | None = None,
        body_decision: str = "DECLINED",
        succeeds: bool = True,
        startup_choice: str = "",
        startup_choice_quote: str = "",
        startup_options: tuple[str, str, str] | None = None,
        custom_profile_quote: str = "",
        menu_surface: str = "TEXT_NUMBERED_MENU",
        reuse_from: Path | None = None,
        reuse_chat_id: str = "",
        reuse_message_id: str = "",
        reviewed_style: int = 1,
    ) -> tuple[Path, Path]:
        guard = self._start_request(request_id) if start_guard else self._guard_path(request_id)
        args: list[object] = [
            "prepare-generation",
            "--workspace", self.workspace, "--style-name", self.style_name,
            "--request-id", request_id, "--fidelity", str(fidelity),
            "--startup-selection-mode", startup_mode, "--aux-body-decision", body_decision,
            "--confirmed-chat-id", "integration-chat", "--confirmed-message-id", "integration-message",
        ]
        if startup_mode == "DIRECT_CONFIRMATION":
            args.extend(("--confirmed-parameters-user-quote", startup_quote or f"Keep the confirmed {fidelity}% fidelity and decline BODY_REFERENCE_LIBRARY."))
        elif startup_mode in {"USER_CONFIRMATION", "NEW"}:
            args.extend(("--startup-menu-surface", menu_surface, "--startup-choice", startup_choice, "--startup-choice-user-quote", startup_choice_quote))
            for option in startup_options or ():
                args.extend(("--startup-option", option))
            if custom_profile_quote:
                args.extend(("--custom-parameters-user-quote", custom_profile_quote))
        elif startup_mode == "REUSE":
            args.extend(("--reuse-startup-from", reuse_from, "--reuse-chat-id", reuse_chat_id, "--reuse-message-id", reuse_message_id))
        args.extend((
            "--generation-purpose", "SCENE", "--character-id", "NONE",
            "--scene-kind", "ARTIFACT", "--scene-output-use", "GENERAL_ART",
            "--scene-subject-from-prompt", "--style-reference", self.style_reference,
            "--reviewed", f"STYLE={reviewed_style}",
            "--override", "FACE", "--override", "BODY", "--override", "POSE",
            "--override", "CLOTHES", "--override", "LIGHTING", "--override", "BACKGROUND",
            "--override", "COMPOSITION",
        ))
        if body_decision == "SELECTED":
            args.extend(("--aux-body-selection-note", "No reviewed reusable body reference is relevant to this artifact scene."))
        self._cli("style_pack_manager.py", *args, succeeds=succeeds)
        plan_path = guard.parent / "REFERENCE_PLAN.json"
        return guard, plan_path

    def test_style_only_yes_cannot_prepare_or_write_reference_plan(self):
        _, plan_path = self._prepare_scene(
            "style-only-yes", startup_mode="DIRECT_CONFIRMATION", body_decision="SELECTED",
            startup_quote="да", succeeds=False,
        )
        self.assertFalse(plan_path.exists())

    def test_unformed_style_accepts_confirmed_request_local_master_candidate_without_promoting_anchor(self):
        metadata_path = self.style_pack / ".style-pack.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["status"] = "REVIEW_REQUIRED"
        metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

        candidate = self.style_pack / "01_WORK" / "STYLE_CROPS" / "MASTER_STYLE_fixture.png"
        candidate.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (96, 128), (40, 50, 60)).save(candidate)
        self.style_reference = candidate
        reference_manifest = self.style_pack / "02_LOCAL_ONLY_DO_NOT_UPLOAD" / "LOGS" / "PRELIMINARY_REFERENCE_MANIFEST.csv"
        with reference_manifest.open("r", encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream)
            fieldnames = reader.fieldnames
            manifest_rows = list(reader)
        candidate_row = {field: "" for field in fieldnames}
        candidate_row.update({
            "stored_relative_path": "01_WORK/STYLE_CROPS/MASTER_STYLE_fixture.png",
            "primary_role": "MASTER_STYLE",
            "status": "TEST",
            "generator_safe": "YES",
        })
        manifest_rows.append(candidate_row)
        with reference_manifest.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(manifest_rows)
        options = (
            "OPTION_1=Recommended 90% fidelity; select BODY_REFERENCE_LIBRARY",
            "OPTION_2=Contextual 90% fidelity; decline BODY_REFERENCE_LIBRARY",
            "OPTION_3=Contextual 70% fidelity; decline BODY_REFERENCE_LIBRARY",
        )
        guard, plan_path = self._prepare_scene(
            "unformed-request-local-style", 90, startup_mode="USER_CONFIRMATION",
            body_decision="SELECTED", startup_choice="OPTION_1", startup_choice_quote="1",
            startup_options=options, reviewed_style=2,
        )
        call = self._resolve_and_prepare_call(
            "unformed-request-local-style", guard, plan_path,
            "Create one synthetic fixture using only the confirmed local style candidate.",
        )
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        selected_style = plan["selected_references"]["style"][0]
        self.assertEqual(selected_style["status"], "REQUEST_LOCAL_STYLE_CANDIDATE")
        self.assertEqual(selected_style["reference_scope"], "CURRENT_REQUEST_ONLY")
        self.assertFalse(selected_style["permanent_anchor"])
        self.assertEqual(json.loads(metadata_path.read_text(encoding="utf-8"))["status"], "REVIEW_REQUIRED")

        with reference_manifest.open("r", encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream)
            fieldnames = reader.fieldnames
            rows = list(reader)
        for row in rows:
            if row.get("stored_relative_path") == "01_WORK/STYLE_CROPS/MASTER_STYLE_fixture.png":
                row["status"] = "REJECTED"
        with reference_manifest.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        prompt = "Create one synthetic fixture using only the confirmed local style candidate."
        self._cli(
            "style_pack_manager.py", "prepare-call", "--workspace", self.workspace,
            "--style-name", self.style_name, "--request-id", "unformed-request-local-style",
            "--prompt-text", prompt, "--risk-assessment", call["risk_assessment"]["path"], succeeds=False,
        )

        with reference_manifest.open("r", encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream)
            fieldnames = reader.fieldnames
            rows = list(reader)
        for row in rows:
            if row.get("stored_relative_path") == "01_WORK/STYLE_CROPS/MASTER_STYLE_fixture.png":
                row["status"], row["generator_safe"] = "TEST", "NO"
        with reference_manifest.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["status"] = "FINALIZED_APPROVED"
        metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        self._cli(
            "style_pack_manager.py", "prepare-call", "--workspace", self.workspace,
            "--style-name", self.style_name, "--request-id", "unformed-request-local-style",
            "--prompt-text", prompt, "--risk-assessment", call["risk_assessment"]["path"], succeeds=False,
        )

        with reference_manifest.open("r", encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream)
            fieldnames = reader.fieldnames
            rows = list(reader)
        for row in rows:
            if row.get("stored_relative_path") == "01_WORK/STYLE_CROPS/MASTER_STYLE_fixture.png":
                row["status"], row["generator_safe"] = "TEST", "NO"
        with reference_manifest.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        from tools import style_pack_manager as manager
        from tools.task_execution_guard import GuardError, validate_reference_plan
        with self.assertRaisesRegex(GuardError, "no longer positive and generator-safe"):
            validate_reference_plan(plan_path)

        with reference_manifest.open("r", encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream)
            fieldnames = reader.fieldnames
            rows = list(reader)
        for row in rows:
            if row.get("stored_relative_path") == "01_WORK/STYLE_CROPS/MASTER_STYLE_fixture.png":
                row["status"], row["generator_safe"] = "", "YES"
        with reference_manifest.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        self.assertFalse(manager.has_positive_master_style_manifest_entry(
            manager.make_paths(self.workspace, self.style_name), candidate.relative_to(self.style_pack),
        ))
        with reference_manifest.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fieldnames)
            writer.writeheader()
            valid_row = {field: "" for field in fieldnames}
            valid_row.update({
                "stored_relative_path": "01_WORK/STYLE_CROPS/MASTER_STYLE_fixture.png",
                "primary_role": "MASTER_STYLE", "status": "TEST", "generator_safe": "YES",
                "user_approved": "NO",
            })
            writer.writerow(valid_row)
            writer.writerow(valid_row)
        self.assertFalse(manager.has_positive_master_style_manifest_entry(
            manager.make_paths(self.workspace, self.style_name), candidate.relative_to(self.style_pack),
        ))

    def test_request_local_style_candidate_rechecks_manifest_at_prepare_call(self):
        metadata_path = self.style_pack / ".style-pack.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["status"] = "REVIEW_REQUIRED"
        metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

        candidate = self.style_pack / "01_WORK" / "STYLE_CROPS" / "MASTER_STYLE_fixture.png"
        candidate.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (96, 128), (40, 50, 60)).save(candidate)
        self.style_reference = candidate
        reference_manifest = self.style_pack / "02_LOCAL_ONLY_DO_NOT_UPLOAD" / "LOGS" / "PRELIMINARY_REFERENCE_MANIFEST.csv"
        with reference_manifest.open("r", encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream)
            fieldnames = reader.fieldnames
            manifest_rows = list(reader)
        candidate_row = {field: "" for field in fieldnames}
        candidate_row.update({
            "stored_relative_path": "01_WORK/STYLE_CROPS/MASTER_STYLE_fixture.png",
            "primary_role": "MASTER_STYLE", "status": "TEST", "generator_safe": "YES",
            "user_approved": "NO",
        })
        manifest_rows.append(candidate_row)
        with reference_manifest.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(manifest_rows)

        options = (
            "OPTION_1=Recommended 90% fidelity; select BODY_REFERENCE_LIBRARY",
            "OPTION_2=Contextual 90% fidelity; decline BODY_REFERENCE_LIBRARY",
            "OPTION_3=Contextual 70% fidelity; decline BODY_REFERENCE_LIBRARY",
        )
        for request_id, status, safe in (
            ("request-local-rejected-after-plan", "REJECTED", "YES"),
            ("request-local-unsafe-after-plan", "TEST", "NO"),
        ):
            with self.subTest(status=status, generator_safe=safe):
                with reference_manifest.open("r", encoding="utf-8", newline="") as stream:
                    reader = csv.DictReader(stream)
                    fieldnames = reader.fieldnames
                    rows = list(reader)
                for row in rows:
                    if row.get("stored_relative_path") == "01_WORK/STYLE_CROPS/MASTER_STYLE_fixture.png":
                        row["status"], row["generator_safe"] = "TEST", "YES"
                with reference_manifest.open("w", encoding="utf-8", newline="") as stream:
                    writer = csv.DictWriter(stream, fieldnames=fieldnames)
                    writer.writeheader()
                    writer.writerows(rows)

                guard, plan_path = self._prepare_scene(
                    request_id, 90, startup_mode="USER_CONFIRMATION", body_decision="SELECTED",
                    startup_choice="OPTION_1", startup_choice_quote="1", startup_options=options, reviewed_style=2,
                )
                self._cli(
                    "style_pack_manager.py", "resolve-call", "--workspace", self.workspace,
                    "--style-name", self.style_name, "--request-id", request_id,
                )
                resolved_path = guard.parent / "TECHNICAL_REFERENCES" / "RESOLVED_CALL_SINGLE_PASS.json"
                resolved = json.loads(resolved_path.read_text(encoding="utf-8"))
                prompt = f"Create one synthetic fixture for {request_id}."
                risk_args: list[object] = [
                    "--text", prompt, "--output", self.workspace / f"{request_id}-risk.json",
                ]
                for slot in resolved["slots"]:
                    role_list = ",".join(slot["active_roles"])
                    risk_args.extend(("--reference", f"{slot['path']}::D2::0D::Synthetic reviewed fixture::{role_list}"))
                risk_path = self.workspace / f"{request_id}-risk.json"
                self._cli("generation_risk_assessor.py", *risk_args)

                with reference_manifest.open("r", encoding="utf-8", newline="") as stream:
                    reader = csv.DictReader(stream)
                    fieldnames = reader.fieldnames
                    rows = list(reader)
                for row in rows:
                    if row.get("stored_relative_path") == "01_WORK/STYLE_CROPS/MASTER_STYLE_fixture.png":
                        row["status"], row["generator_safe"] = status, safe
                with reference_manifest.open("w", encoding="utf-8", newline="") as stream:
                    writer = csv.DictWriter(stream, fieldnames=fieldnames)
                    writer.writeheader()
                    writer.writerows(rows)

                self._cli(
                    "style_pack_manager.py", "prepare-call", "--workspace", self.workspace,
                    "--style-name", self.style_name, "--request-id", request_id,
                    "--prompt-text", prompt, "--risk-assessment", risk_path, succeeds=False,
                )
                plan = json.loads(plan_path.read_text(encoding="utf-8"))
                self.assertEqual(plan["gate_status"], "PREPARED_AWAITING_EXECUTABLE_CALL")
                self.assertIsNone(plan["execution_call"])
                guard_state = json.loads(guard.read_text(encoding="utf-8"))
                self.assertNotIn("ready_binding", guard_state)
    def test_numbered_native_custom_and_reused_profiles_reach_ready_call(self):
        options = (
            "OPTION_1=Recommended 90% fidelity; select BODY_REFERENCE_LIBRARY",
            "OPTION_2=Contextual 90% fidelity; decline BODY_REFERENCE_LIBRARY",
            "OPTION_3=Contextual 70% fidelity; decline BODY_REFERENCE_LIBRARY",
        )
        cases = (
            ("menu-option-1", 90, "SELECTED", "OPTION_1", "1", "USER_CONFIRMATION", "TEXT_NUMBERED_MENU", ""),
            ("menu-option-2", 90, "DECLINED", "OPTION_2", "2", "USER_CONFIRMATION", "TEXT_NUMBERED_MENU", ""),
            ("menu-option-3", 70, "DECLINED", "OPTION_3", "3", "USER_CONFIRMATION", "TEXT_NUMBERED_MENU", ""),
            ("menu-custom", 50, "SELECTED", "CUSTOM", "4", "USER_CONFIRMATION", "TEXT_NUMBERED_MENU", "Use 50% fidelity and select BODY_REFERENCE_LIBRARY."),
            ("menu-custom-natural-en", 50, "SELECTED", "CUSTOM", "Use 50% fidelity and select BODY_REFERENCE_LIBRARY.", "USER_CONFIRMATION", "TEXT_NUMBERED_MENU", ""),
            ("menu-custom-natural-ru", 70, "DECLINED", "CUSTOM", "Хочу 70% стиля без BODY_REFERENCE_LIBRARY.", "USER_CONFIRMATION", "TEXT_NUMBERED_MENU", ""),
            ("menu-native-new", 90, "SELECTED", "OPTION_1", "OPTION_1", "NEW", "NATIVE_CONTEXT_MENU", ""),
        )
        source_plan: Path | None = None
        for request_id, fidelity, decision, choice, reply, mode, surface, custom_quote in cases:
            with self.subTest(request_id=request_id):
                guard, plan_path = self._prepare_scene(
                    request_id, fidelity, startup_mode=mode, body_decision=decision,
                    startup_choice=choice, startup_choice_quote=reply, startup_options=options,
                    custom_profile_quote=custom_quote, menu_surface=surface,
                )
                plan = json.loads(plan_path.read_text(encoding="utf-8"))
                startup = plan["startup_parameter_selection"]
                self.assertEqual(startup["user_choice_quote"], reply)
                self.assertEqual(startup["resolved_parameters"]["fidelity"], fidelity)
                self.assertEqual(startup["resolved_parameters"]["aux_body_decision"], decision)
                if choice == "CUSTOM":
                    self.assertEqual(startup["custom_parameters_user_quote"], custom_quote or reply)
                prompt = f"Create one synthetic observatory fixture for {request_id}."
                self._resolve_and_prepare_call(request_id, guard, plan_path, prompt)
                if request_id == "menu-option-1":
                    source_plan = plan_path

        assert source_plan is not None
        source = json.loads(source_plan.read_text(encoding="utf-8"))["startup_parameter_selection"]
        profile = source["resolved_parameters"]
        for request_id, reused_from in (("menu-reuse-once", source_plan),):
            guard, plan_path = self._prepare_scene(
                request_id, profile["fidelity"], startup_mode="REUSE", body_decision=profile["aux_body_decision"],
                reuse_from=reused_from, reuse_chat_id=source["confirmed_provenance"]["chat_id"],
                reuse_message_id=source["confirmed_provenance"]["message_id"],
            )
            call = self._resolve_and_prepare_call(request_id, guard, plan_path, f"Create one synthetic observatory fixture for {request_id}.")
            self.assertEqual(json.loads(plan_path.read_text(encoding="utf-8"))["startup_parameter_selection"]["selected"], "OPTION_1")
            source_plan = plan_path

        assert source_plan is not None
        repeated = json.loads(source_plan.read_text(encoding="utf-8"))["startup_parameter_selection"]
        guard, plan_path = self._prepare_scene(
            "menu-reuse-twice", repeated["resolved_parameters"]["fidelity"], startup_mode="REUSE",
            body_decision=repeated["resolved_parameters"]["aux_body_decision"],
            reuse_from=source_plan, reuse_chat_id=repeated["confirmed_provenance"]["chat_id"],
            reuse_message_id=repeated["confirmed_provenance"]["message_id"],
        )
        self._resolve_and_prepare_call("menu-reuse-twice", guard, plan_path, "Create one synthetic observatory fixture for repeated reuse.")

    def _resolve_and_prepare_call(self, request_id: str, guard: Path, plan_path: Path, prompt: str, stage_id: str = "") -> dict:
        resolve_args: list[object] = [
            "resolve-call", "--workspace", self.workspace, "--style-name", self.style_name,
            "--request-id", request_id,
        ]
        if stage_id:
            resolve_args.extend(("--stage-id", stage_id))
        self._cli("style_pack_manager.py", *resolve_args)
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        selected_stage = stage_id or "SINGLE_PASS"
        resolved_manifest = guard.parent / "TECHNICAL_REFERENCES" / f"RESOLVED_CALL_{selected_stage}.json"
        resolved = json.loads(resolved_manifest.read_text(encoding="utf-8"))
        self.assertEqual(resolved["request_id"], request_id)
        self.assertTrue(resolved["slots"])
        risk_args: list[object] = ["--text", prompt, "--output", self.workspace / f"{request_id}-{selected_stage}-risk.json"]
        for slot in resolved["slots"]:
            role_list = ",".join(slot["active_roles"])
            risk_args.extend(("--reference", f"{slot['path']}::D2::0D::Synthetic reviewed fixture::{role_list}"))
        risk_path = Path(risk_args[risk_args.index("--output") + 1])
        self._cli("generation_risk_assessor.py", *risk_args)
        call_args: list[object] = [
            "prepare-call", "--workspace", self.workspace, "--style-name", self.style_name,
            "--request-id", request_id, "--prompt-text", prompt, "--risk-assessment", risk_path,
        ]
        if stage_id:
            call_args.extend(("--stage-id", stage_id))
        self._cli("style_pack_manager.py", *call_args)
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        self.assertEqual(plan["gate_status"], "READY_FOR_GENERATION")
        call = plan["execution_call"]
        self.assertTrue(call["slots"])
        self.assertEqual(call["risk_assessment"]["generation_risk"], plan["risk_assessment"]["generation_risk"])
        return call

    def _start_call(self, guard: Path, plan_path: Path, call: dict) -> str:
        self._cli(
            "task_execution_guard.py", "checkpoint", "--state", guard, "--event", "CALL_VALIDATED",
            "--summary", "Synthetic integration call was validated.",
        )
        args: list[object] = [
            "checkpoint", "--state", guard, "--event", "EXECUTION_STARTED",
            "--summary", "Synthetic integration call started.", "--reference-plan", plan_path,
            "--stage", call["stage_id"], "--output-contract", "REQUESTED_DELIVERABLE",
        ]
        if call["stage_id"] in {"02_PHYSIQUE_FRONT", "03_PHYSIQUE_SIDE", "04_PHYSIQUE_BACK"}:
            args.extend(("--swimwear-rung", "EXTREME_MICRO"))
        self._cli("task_execution_guard.py", *args)
        state = json.loads(guard.read_text(encoding="utf-8"))
        return state["active_attempt"]["attempt_id"]

    def _risk_level(self, plan_path: Path) -> str:
        return json.loads(plan_path.read_text(encoding="utf-8"))["risk_assessment"]["generation_risk"]

    def test_qa_stage_contract_must_match_manifest_stage_marker(self):
        from unittest.mock import patch
        from tools.style_pack_manager import make_paths, validated_stage_outputs

        paths = make_paths(self.workspace, self.style_name)
        request_id = "qa-stage-provenance"
        plan_path = paths.generations / "00_PENDING" / request_id / "REFERENCE_PLAN.json"
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text(json.dumps({"request_id": request_id, "character_id": "NEW"}), encoding="utf-8")
        output = self.workspace / "qa-passed-face.png"
        Image.new("RGB", (32, 32), (10, 20, 30)).save(output)
        snapshot = self.workspace / "qa-plan-snapshot.json"
        snapshot.write_text(json.dumps({"request_id": request_id, "character_id": "NEW"}), encoding="utf-8")
        contract = self.workspace / "qa-contract.json"
        contract.write_text(json.dumps({"stage_id": "02_PHYSIQUE_FRONT", "plan_snapshot": str(snapshot)}), encoding="utf-8")
        with paths.generation_manifest.open("w", encoding="utf-8-sig", newline="") as manifest_file:
            fields = ("request_id", "character_id", "status", "style_file", "reference_plan", "notes", "qa_evidence")
            writer = csv.DictWriter(manifest_file, fieldnames=fields)
            writer.writeheader()
            writer.writerow({
                "request_id": request_id, "character_id": "NEW", "status": "STAGING",
                "style_file": str(output), "reference_plan": str(plan_path.resolve()),
                "notes": "[STAGE_ID=01_FACE_IDENTITY]", "qa_evidence": "mocked-valid-receipt",
            })
        def receipt(_row):
            return {"record_status": "STAGING", "qa_failed": [], "qa_contract": str(contract)}
        with patch("tools.style_pack_manager.generation_qa_evidence", side_effect=receipt):
            outputs = validated_stage_outputs(paths, plan_path, {"request_id": request_id, "character_id": "NEW"})
        self.assertNotIn("01_FACE_IDENTITY", outputs)

    def test_newest_rejected_stage_row_blocks_older_qa_passed_output(self):
        from unittest.mock import patch
        from tools.style_pack_manager import make_paths, validated_stage_outputs

        paths = make_paths(self.workspace, self.style_name)
        request_id = "latest-stage-authority"
        plan_path = self.workspace / "REFERENCE_PLAN.json"
        plan_path.write_text(json.dumps({"request_id": request_id, "character_id": "NEW"}), encoding="utf-8")
        older = self.workspace / "older-stage.png"
        newer = self.workspace / "newer-rejected-stage.png"
        Image.new("RGB", (32, 32), (10, 20, 30)).save(older)
        Image.new("RGB", (32, 32), (30, 20, 10)).save(newer)
        with paths.generation_manifest.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=("request_id", "status", "style_file", "reference_plan", "notes", "qa_evidence"))
            writer.writeheader()
            writer.writerow({"request_id": request_id, "status": "STAGING", "style_file": str(older), "reference_plan": str(plan_path.resolve()), "notes": "[STAGE_ID=01_FACE_IDENTITY]", "qa_evidence": "valid"})
            writer.writerow({"request_id": request_id, "status": "REJECTED", "style_file": str(newer), "reference_plan": str(plan_path.resolve()), "notes": "[STAGE_ID=01_FACE_IDENTITY]", "qa_evidence": "corrupt"})

        def receipt(row):
            return {"record_status": "STAGING", "qa_failed": []} if row.get("qa_evidence") == "valid" else None

        with patch("tools.style_pack_manager.generation_qa_evidence", side_effect=receipt):
            outputs = validated_stage_outputs(paths, plan_path, {"request_id": request_id, "character_id": "NEW"})
        self.assertNotIn("01_FACE_IDENTITY", outputs)

    def test_scene_cli_stays_prepared_until_exact_call_and_result_waits_for_delivery(self):
        request_id = "scene-happy"
        guard, plan_path = self._prepare_scene(request_id)
        prepared = json.loads(plan_path.read_text(encoding="utf-8"))
        self.assertEqual(prepared["gate_status"], "PREPARED_AWAITING_EXECUTABLE_CALL")
        self.assertIsNone(prepared["execution_call"])
        self.assertEqual(prepared["startup_parameter_selection"]["parameter_source"], "DIRECT_USER_CONFIRMED_CURRENT_CHAT")
        self.assertEqual(prepared["generation_workflow"]["mode"], "SINGLE_PASS")
        self.assertNotIn("stages", prepared["generation_workflow"], "SCENE + NONE must not create helper art stages.")

        prompt = "Create one synthetic stone observatory artifact on a plain dark backdrop."
        call = self._resolve_and_prepare_call(request_id, guard, plan_path, prompt)
        ready_plan_bytes = plan_path.read_bytes()
        ready_binding = json.loads(guard.read_text(encoding="utf-8"))["ready_binding"]
        risk_path = call["risk_assessment"]["path"]
        same_call_retry = self._cli(
            "style_pack_manager.py", "prepare-call", "--workspace", self.workspace,
            "--style-name", self.style_name, "--request-id", request_id,
            "--prompt-text", prompt, "--risk-assessment", risk_path,
        )
        self.assertIn("unchanged retry", same_call_retry.stdout)
        self.assertEqual(plan_path.read_bytes(), ready_plan_bytes)
        self.assertEqual(json.loads(guard.read_text(encoding="utf-8"))["ready_binding"], ready_binding)
        changed_call = self._cli(
            "style_pack_manager.py", "prepare-call", "--workspace", self.workspace,
            "--style-name", self.style_name, "--request-id", request_id,
            "--prompt-text", prompt + " Change the camera angle.", "--risk-assessment", risk_path,
            succeeds=False,
        )
        self.assertTrue(changed_call.stderr.strip())
        self.assertEqual(plan_path.read_bytes(), ready_plan_bytes)
        self.assertEqual(json.loads(guard.read_text(encoding="utf-8"))["ready_binding"], ready_binding)
        attempt_id = self._start_call(guard, plan_path, call)
        result = self.workspace / "synthetic-scene.png"
        Image.new("RGB", (720, 1280), (65, 72, 84)).save(result)
        qa = (
            "--qa-attachments", "PASS", "--qa-canvas", "PASS", "--qa-stage-layer", "PASS",
            "--qa-style", "PASS", "--qa-subject-accuracy", "PASS", "--qa-no-unrequested-characters", "PASS",
            "--qa-focal-hierarchy", "PASS", "--qa-lighting", "PASS", "--qa-background", "PASS",
            "--qa-composition", "PASS", "--qa-depth-and-scale", "PASS", "--qa-artifact-integrity", "PASS",
        )
        recorded = self._cli(
            "style_pack_manager.py", "record-generation", "--workspace", self.workspace,
            "--style-name", self.style_name, "--image", result, "--request-id", request_id,
            "--description", "Synthetic integration scene", "--fidelity", "70", "--status", "TEST",
            "--character-id", "NONE", "--attempt-id", attempt_id, "--reference-plan", plan_path, *qa,
        )
        self.assertIn("STATUS=TEST", recorded.stdout)
        state = json.loads(guard.read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "ACTIVE")
        self.assertEqual(state["phase"], "RESULT_AVAILABLE")
        self.assertIsNone(state["delivered_result_at"])
        self.assertNotEqual(state["status"], "COMPLETE")

    def test_reconciled_available_result_records_once_and_rejects_stale_evidence(self):
        request_id = "reconciled-available"
        guard, plan_path = self._prepare_scene(request_id)
        call = self._resolve_and_prepare_call(request_id, guard, plan_path, "Create one reconciled synthetic artifact.")
        attempt_id = self._start_call(guard, plan_path, call)
        result = self.workspace / "reconciled-artifact.png"
        Image.new("RGB", (720, 1280), (65, 72, 84)).save(result)
        self._cli(
            "task_execution_guard.py", "checkpoint", "--state", guard, "--event", "ATTEMPT_UNKNOWN",
            "--summary", "Synthetic provider timeout.", "--attempt-id", attempt_id,
        )
        self._cli(
            "task_execution_guard.py", "checkpoint", "--state", guard, "--event", "ATTEMPT_RECONCILED",
            "--summary", "Synthetic provider receipt confirms the output exists.", "--attempt-id", attempt_id,
            "--outcome", "AVAILABLE", "--result-status", "TEST", "--evidence", result,
            "--reconciliation-evidence", "synthetic-provider-receipt-001",
        )
        fixed_evidence_hash = hashlib.sha256(result.read_bytes()).hexdigest()
        reconciled_state = json.loads(guard.read_text(encoding="utf-8"))
        self.assertEqual(reconciled_state["available_results"][-1]["evidence_sha256"], [fixed_evidence_hash])
        original_result = result.read_bytes()
        Image.new("RGB", (720, 1280), (9, 8, 7)).save(result)
        qa = (
            "--qa-attachments", "PASS", "--qa-canvas", "PASS", "--qa-stage-layer", "PASS",
            "--qa-style", "PASS", "--qa-subject-accuracy", "PASS", "--qa-no-unrequested-characters", "PASS",
            "--qa-focal-hierarchy", "PASS", "--qa-lighting", "PASS", "--qa-background", "PASS",
            "--qa-composition", "PASS", "--qa-depth-and-scale", "PASS", "--qa-artifact-integrity", "PASS",
        )
        stale = self.workspace / "stale-reconciled-artifact.png"
        Image.new("RGB", (720, 1280), (12, 14, 18)).save(stale)
        rejected = self._cli(
            "style_pack_manager.py", "record-generation", "--workspace", self.workspace,
            "--style-name", self.style_name, "--image", stale, "--request-id", request_id,
            "--description", "Synthetic stale evidence", "--fidelity", "70", "--status", "TEST",
            "--character-id", "NONE", "--attempt-id", attempt_id, "--reference-plan", plan_path, *qa,
            succeeds=False,
        )
        self.assertIn("exact path recorded", rejected.stderr)
        changed_same_path = self._cli(
            "style_pack_manager.py", "record-generation", "--workspace", self.workspace,
            "--style-name", self.style_name, "--image", result, "--request-id", request_id,
            "--description", "Synthetic changed reconciled scene", "--fidelity", "70", "--status", "TEST",
            "--character-id", "NONE", "--attempt-id", attempt_id, "--reference-plan", plan_path, *qa,
            succeeds=False,
        )
        self.assertIn("exact path recorded", changed_same_path.stderr)
        result.write_bytes(original_result)
        common = (
            "style_pack_manager.py", "record-generation", "--workspace", self.workspace,
            "--style-name", self.style_name, "--image", result, "--request-id", request_id,
            "--description", "Synthetic reconciled scene", "--fidelity", "70", "--status", "TEST",
            "--character-id", "NONE", "--attempt-id", attempt_id, "--reference-plan", plan_path, *qa,
        )
        first = self._cli(*common)
        retry = self._cli(*common)
        self.assertIn("STATUS=TEST", first.stdout)
        self.assertIn("UNCHANGED_RETRY=true", retry.stdout)
        state = json.loads(guard.read_text(encoding="utf-8"))
        self.assertEqual(state["phase"], "ATTEMPT_AVAILABLE")
        self.assertEqual(len(state["available_results"]), 1)
        with (self.generations / "GENERATION_MANIFEST.csv").open(encoding="utf-8-sig", newline="") as stream:
            rows = [row for row in csv.DictReader(stream) if row["request_id"] == request_id]
        self.assertEqual(len(rows), 1)

    def test_wrong_request_attempt_stage_character_and_low_fidelity_missing_qa_are_rejected(self):
        prompt = "Create one synthetic stone observatory artifact on a plain dark backdrop."
        semantic_qa = (
            "--qa-attachments", "PASS", "--qa-canvas", "PASS", "--qa-stage-layer", "PASS",
            "--qa-style", "PASS", "--qa-subject-accuracy", "PASS", "--qa-no-unrequested-characters", "PASS",
            "--qa-focal-hierarchy", "PASS", "--qa-lighting", "PASS", "--qa-background", "PASS",
            "--qa-composition", "PASS", "--qa-depth-and-scale", "PASS", "--qa-artifact-integrity", "PASS",
        )
        def start_negative(request_id: str):
            guard, plan_path = self._prepare_scene(request_id, fidelity=30)
            call = self._resolve_and_prepare_call(request_id, guard, plan_path, prompt)
            attempt_id = self._start_call(guard, plan_path, call)
            output = self.workspace / f"{request_id}-output.png"
            Image.new("RGB", (120, 180), (20, 24, 28)).save(output)
            common: list[object] = [
                "record-generation", "--workspace", self.workspace, "--style-name", self.style_name,
                "--image", output, "--description", "Synthetic negative fixture", "--fidelity", "30",
                "--risk-level", self._risk_level(plan_path), "--character-id", "NONE",
                "--attempt-id", attempt_id, "--reference-plan", plan_path,
            ]
            return guard, common

        # Each failing result can archive its attempt, so exercise each contract
        # against a fresh request rather than chaining state-mutating failures.
        guard, common = start_negative("negative-request")
        self._cli("style_pack_manager.py", *common, "--request-id", "wrong-request", succeeds=False)
        mismatch = self._cli(
            "style_pack_manager.py", *common, "--request-id", "negative-request", "--character-id", "CHAR_001",
            succeeds=False,
        )
        self.assertIn("does not match the prepared plan", mismatch.stderr)
        mismatch_state = json.loads(guard.read_text(encoding="utf-8"))
        self.assertIsNone(mismatch_state["delivered_result_at"])
        self.assertIsNotNone(mismatch_state["active_attempt"], "A metadata correction must not consume the active attempt.")
        corrected = self._cli(
            "style_pack_manager.py", *common, "--request-id", "negative-request", *semantic_qa,
        )
        self.assertIn("STATUS=TEST", corrected.stdout)
        corrected_state = json.loads(guard.read_text(encoding="utf-8"))
        self.assertEqual(corrected_state["attempts"][-1]["attempt_id"], common[common.index("--attempt-id") + 1])
        self.assertEqual(corrected_state["phase"], "RESULT_AVAILABLE")

        _, common = start_negative("negative-attempt")
        wrong_attempt = self._cli(
            "style_pack_manager.py", *common, "--request-id", "negative-attempt", "--attempt-id", "wrong-attempt",
            succeeds=False,
        )
        self.assertTrue(wrong_attempt.stderr.strip())

        _, common = start_negative("negative-stage")
        wrong_stage = self._cli(
            "style_pack_manager.py", *common, "--request-id", "negative-stage", "--stage-id", "WRONG_STAGE",
            succeeds=False,
        )
        self.assertTrue(wrong_stage.stderr.strip())

        guard, common = start_negative("negative-low-fidelity-qa")
        low_fidelity = self._cli(
            "style_pack_manager.py", *common, "--request-id", "negative-low-fidelity-qa", succeeds=False,
        )
        self.assertIn("Required post-generation QA was not performed", low_fidelity.stderr)
        with (self.generations / "GENERATION_MANIFEST.csv").open(encoding="utf-8-sig", newline="") as manifest_file:
            rows = list(csv.DictReader(manifest_file))
        self.assertFalse(any(row["request_id"] == "negative-low-fidelity-qa" for row in rows))
        self.assertIsNotNone(json.loads(guard.read_text(encoding="utf-8"))["active_attempt"])
        retried = self._cli(
            "style_pack_manager.py", *common, "--request-id", "negative-low-fidelity-qa", *semantic_qa,
        )
        self.assertIn("STATUS=TEST", retried.stdout)
        final_state = json.loads(guard.read_text(encoding="utf-8"))
        self.assertEqual(final_state["attempts"][-1]["attempt_id"], common[common.index("--attempt-id") + 1])
        self.assertEqual(final_state["phase"], "RESULT_AVAILABLE")

    def test_record_generation_recovers_exact_already_visible_output(self):
        request_id = "visible-before-record"
        guard, plan_path = self._prepare_scene(request_id, fidelity=30)
        prompt = "Create one synthetic stone observatory artifact on a plain dark backdrop."
        call = self._resolve_and_prepare_call(request_id, guard, plan_path, prompt)
        attempt_id = self._start_call(guard, plan_path, call)
        provider_output = self.workspace / "provider-output.png"
        Image.new("RGB", (240, 320), (65, 72, 84)).save(provider_output)

        from tools import style_pack_manager as manager

        paths = manager.make_paths(self.workspace, self.style_name)
        archive = manager.archive_generation(paths, provider_output, "already visible fixture")
        self._cli(
            "task_execution_guard.py", "checkpoint", "--state", guard, "--event", "VISIBLE_RESULT",
            "--summary", "The exact provider image is already visible.", "--attempt-id", attempt_id,
            "--result-status", "TEST", "--evidence", provider_output,
        )
        state_before = json.loads(guard.read_text(encoding="utf-8"))
        self.assertEqual(state_before["attempts"][-1]["status"], "RESULT_AVAILABLE")
        self.assertEqual(state_before["attempts"][-1]["result_evidence"], [str(provider_output.resolve())])

        wrong_archive = self.workspace / "GENERATION_RESULTS" / "different-content.png"
        wrong_archive.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (240, 320), (12, 34, 56)).save(wrong_archive)

        qa = (
            "--qa-attachments", "PASS", "--qa-canvas", "PASS", "--qa-stage-layer", "PASS",
            "--qa-style", "PASS", "--qa-subject-accuracy", "PASS", "--qa-no-unrequested-characters", "PASS",
            "--qa-focal-hierarchy", "PASS", "--qa-lighting", "PASS", "--qa-background", "PASS",
            "--qa-composition", "PASS", "--qa-depth-and-scale", "PASS", "--qa-artifact-integrity", "PASS",
        )
        record_args = (
            "style_pack_manager.py", "record-generation", "--workspace", self.workspace,
            "--style-name", self.style_name, "--request-id", request_id,
            "--description", "Synthetic visible-result recovery", "--fidelity", "30", "--status", "TEST",
            "--risk-level", self._risk_level(plan_path), "--character-id", "NONE",
            "--attempt-id", attempt_id, "--reference-plan", plan_path, *qa,
        )
        rejected = self._cli(
            *record_args, "--image", wrong_archive, succeeds=False,
        )
        self.assertTrue(rejected.stderr.strip())
        self.assertEqual(len(list((self.workspace / "GENERATION_RESULTS").iterdir())), 2)
        with (self.generations / "GENERATION_MANIFEST.csv").open(encoding="utf-8-sig", newline="") as manifest_file:
            self.assertFalse(any(row["request_id"] == request_id for row in csv.DictReader(manifest_file)))

        recorded = self._cli(*record_args, "--image", archive)
        self.assertIn("STATUS=TEST", recorded.stdout)
        with (self.generations / "GENERATION_MANIFEST.csv").open(encoding="utf-8-sig", newline="") as manifest_file:
            rows = [row for row in csv.DictReader(manifest_file) if row["request_id"] == request_id]
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(Path(row["archive_file"]).resolve(), archive.resolve())
        self.assertTrue(Path(row["style_file"]).is_file())
        self.assertTrue(row["qa_evidence"])
        self.assertIsNotNone(manager.generation_qa_evidence(row))
        self.assertEqual(len(list((self.workspace / "GENERATION_RESULTS").iterdir())), 2)
        state_after = json.loads(guard.read_text(encoding="utf-8"))
        visible_events = [event for event in state_after["events"] if event.get("event") == "VISIBLE_RESULT"]
        self.assertEqual(len(visible_events), 1)

    def test_all_five_canonical_stages_resolve_packs_pass_qa_and_complete(self):
        request_id = "multistage-continuation"
        stages = (
            "01_FACE_IDENTITY", "02_PHYSIQUE_FRONT", "03_PHYSIQUE_SIDE",
            "04_PHYSIQUE_BACK", "05_CHARACTER_ASSEMBLY",
        )
        guard = self._start_request(request_id, required_stages=stages)
        prep_args: list[object] = [
            "prepare-generation", "--workspace", self.workspace, "--style-name", self.style_name,
            "--request-id", request_id, "--fidelity", "70", "--confirmed-chat-id", "integration-chat",
            "--startup-selection-mode", "DIRECT_CONFIRMATION", "--aux-body-decision", "DECLINED",
            "--confirmed-message-id", "integration-message",
            "--confirmed-parameters-user-quote", "Keep 70% fidelity and decline BODY_REFERENCE_LIBRARY.",
            "--generation-purpose", "CHARACTER_BASE", "--character-id", "NEW", "--character-name", "Synthetic", "--adult-character",
            "--orientation", "PORTRAIT", "--framing", "FULL_BODY", "--target-pose-family", "STANDING",
            "--dominant-body-source", "PROMPT_BODY_SPEC", "--body-source-coverage", "FULL_BODY",
            "--body-source-pose-family", "STANDING", "--body-height-heads", "7.2-7.5",
            "--body-silhouette-notes", "Synthetic stable proportions for test fixture.", "--prompt-only-physique",
            "--style-reference", self.style_reference, "--primary-face", self.primary_face,
            "--supporting-face", self.supporting_face, "--reviewed", "STYLE=1",
            "--reviewed", "FACE=2",
            "--override", "CLOTHES", "--override", "LIGHTING", "--override", "BACKGROUND", "--override", "COMPOSITION",
        ]
        self._cli("style_pack_manager.py", *prep_args)
        plan_path = guard.parent / "REFERENCE_PLAN.json"
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        self.assertEqual(plan["generation_workflow"]["mode"], "MULTI_STAGE")
        self.assertEqual(len(plan["generation_workflow"]["stages"]), 5)

        face_prompt = "Resolve the synthetic adult character face in the selected drawing style."
        face_call = self._resolve_and_prepare_call(request_id, guard, plan_path, face_prompt, "01_FACE_IDENTITY")
        face_attempt = self._start_call(guard, plan_path, face_call)
        face_output = self.workspace / "face-stage.png"
        Image.new("RGB", (512, 768), (185, 125, 112)).save(face_output)
        self._cli(
            "style_pack_manager.py", "record-generation", "--workspace", self.workspace,
            "--style-name", self.style_name, "--image", face_output, "--request-id", request_id,
            "--description", "Synthetic face stage", "--fidelity", "70", "--status", "STAGING",
            "--character-id", "NEW", "--attempt-id", face_attempt, "--reference-plan", plan_path,
            "--stage-id", "01_FACE_IDENTITY", "--qa-attachments", "PASS", "--qa-canvas", "PASS",
            "--qa-stage-layer", "PASS", "--qa-face", "PASS", "--qa-expression", "PASS",
            "--qa-style", "PASS", "--qa-neutral-backdrop", "PASS",
        )
        with (self.generations / "GENERATION_MANIFEST.csv").open(encoding="utf-8-sig", newline="") as manifest_file:
            recorded_rows = list(csv.DictReader(manifest_file))
        self.assertEqual(recorded_rows[-1]["status"], "STAGING")
        self.assertTrue(recorded_rows[-1]["qa_evidence"])
        copied_face = Path(recorded_rows[-1]["style_file"])
        self._cli(
            "task_execution_guard.py", "checkpoint", "--state", guard, "--event", "STAGE_COMPLETED",
            "--summary", "Synthetic first stage has a durable QA-passed record.",
            "--stage", "01_FACE_IDENTITY", "--evidence", copied_face,
        )

        front_prompt = "Project the verified face into the canonical full-body front view."
        front_call = self._resolve_and_prepare_call(request_id, guard, plan_path, front_prompt, "02_PHYSIQUE_FRONT")
        face_binding = next(binding for binding in front_call["stage_output_bindings"] if binding["stage_id"] == "01_FACE_IDENTITY")
        self.assertEqual(face_binding["sha256"], hashlib.sha256(copied_face.read_bytes()).hexdigest())
        manifest_path = self.generations / "GENERATION_MANIFEST.csv"
        original_manifest = manifest_path.read_bytes()
        with manifest_path.open(encoding="utf-8-sig", newline="") as manifest_file:
            rows = list(csv.DictReader(manifest_file))
            fields = list(rows[0].keys())
        stale_row = dict(rows[-1])
        stale_row["status"] = "REJECTED"
        stale_row["qa_evidence"] = "corrupt receipt"
        stale_row["style_file"] = str(self.workspace / "newer-rejected-face.png")
        Image.new("RGB", (32, 32), (1, 2, 3)).save(stale_row["style_file"])
        with manifest_path.open("w", encoding="utf-8-sig", newline="") as manifest_file:
            writer = csv.DictWriter(manifest_file, fieldnames=fields)
            writer.writeheader()
            writer.writerows([*rows, stale_row])
        self._cli(
            "task_execution_guard.py", "checkpoint", "--state", guard, "--event", "EXECUTION_STARTED",
            "--summary", "Synthetic multi-stage call started.", "--reference-plan", plan_path,
            "--stage", front_call["stage_id"], "--output-contract", "REQUESTED_DELIVERABLE",
            "--swimwear-rung", "EXTREME_MICRO", succeeds=False,
        )
        manifest_path.write_bytes(original_manifest)
        front_attempt = self._start_call(guard, plan_path, front_call)
        front_output = self.workspace / "front-stage.png"
        Image.new("RGB", (720, 1280), (145, 118, 104)).save(front_output)
        limb_evidence = (
            "source=PROMPT_BODY_SPEC;view=FRONT;head_units=7.3;pubic_height_fraction=0.5;hip_knee=1.0;"
            "knee_ankle=1.0;ankle_width=0.2;foot_length=1.0;foot_pose=FLAT;foot_length_mode=MEASURED;"
            "hip_landmark=FEMORAL_HEAD_CENTER;knee_landmark=KNEE_JOINT_CENTER;ankle_landmark=TALOCRURAL_JOINT_CENTER;"
            "landmark_confidence=0.95;crown_landmark=CRANIAL_VERTEX;crown_confidence=0.95;"
            "heel_endpoint=VISIBLE;toe_endpoint=VISIBLE;neck_head_ratio=0.3;neck_jaw_ratio=0.7"
        )
        self._cli(
            "style_pack_manager.py", "record-generation", "--workspace", self.workspace,
            "--style-name", self.style_name, "--image", front_output, "--request-id", request_id,
            "--description", "Synthetic front physique stage", "--fidelity", "70", "--status", "STAGING",
            "--character-id", "NEW", "--attempt-id", front_attempt, "--reference-plan", plan_path,
            "--stage-id", "02_PHYSIQUE_FRONT", "--qa-attachments", "PASS", "--qa-canvas", "PASS",
            "--qa-stage-layer", "PASS", "--qa-face", "PASS", "--qa-body-silhouette", "PASS",
            "--qa-body-proportions", "PASS", "--qa-limb-proportions", "PASS", "--limb-qa-evidence", limb_evidence,
            "--qa-view", "PASS", "--qa-safe-coverage", "PASS", "--qa-clothing-topology", "PASS",
            "--qa-style", "PASS", "--qa-body-style", "PASS",
        )
        with (self.generations / "GENERATION_MANIFEST.csv").open(encoding="utf-8-sig", newline="") as manifest_file:
            front_rows = list(csv.DictReader(manifest_file))
        self.assertEqual(front_rows[-1]["status"], "STAGING")
        copied_front = Path(front_rows[-1]["style_file"])
        self._cli(
            "task_execution_guard.py", "checkpoint", "--state", guard, "--event", "STAGE_COMPLETED",
            "--summary", "Synthetic front stage has a durable QA-passed record.",
            "--stage", "02_PHYSIQUE_FRONT", "--evidence", copied_front,
            "--swimwear-rung", "EXTREME_MICRO", "--observed-topology", "EXTREME_MICRO",
        )

        side_prompt = "Create the canonical side view while preserving the verified face and front proportions."
        side_call = self._resolve_and_prepare_call(request_id, guard, plan_path, side_prompt, "03_PHYSIQUE_SIDE")
        self.assertEqual(len(side_call["targeted_pack_bindings"]), 1)
        pack_binding = side_call["targeted_pack_bindings"][0]
        manifest = json.loads(Path(pack_binding["manifest_path"]).read_text(encoding="utf-8"))
        self.assertEqual(pack_binding["request_id"], request_id)
        self.assertEqual([source["sha256"] for source in manifest["sources"]], [
            hashlib.sha256(copied_face.read_bytes()).hexdigest(),
            hashlib.sha256(copied_front.read_bytes()).hexdigest(),
        ])
        self.assertTrue(Path(pack_binding["path"]).is_file())
        self.assertTrue(all(Path(source["path"]).is_file() for source in manifest["sources"]))
        self.assertEqual(json.loads(guard.read_text(encoding="utf-8"))["status"], "READY")

        side_attempt = self._start_call(guard, plan_path, side_call)
        side_output = self.workspace / "side-stage.png"
        Image.new("RGB", (720, 1280), (144, 116, 102)).save(side_output)
        side_limb_evidence = limb_evidence.replace("view=FRONT;", "view=SIDE;")
        self._cli(
            "style_pack_manager.py", "record-generation", "--workspace", self.workspace,
            "--style-name", self.style_name, "--image", side_output, "--request-id", request_id,
            "--description", "Synthetic side physique stage", "--fidelity", "70", "--status", "STAGING",
            "--character-id", "NEW", "--attempt-id", side_attempt, "--reference-plan", plan_path,
            "--stage-id", "03_PHYSIQUE_SIDE", "--qa-attachments", "PASS", "--qa-canvas", "PASS",
            "--qa-stage-layer", "PASS", "--qa-face", "PASS", "--qa-body-silhouette", "PASS",
            "--qa-body-proportions", "PASS", "--qa-limb-proportions", "PASS", "--limb-qa-evidence", side_limb_evidence,
            "--qa-view", "PASS", "--qa-safe-coverage", "PASS", "--qa-clothing-topology", "PASS",
            "--qa-multiview-consistency", "PASS", "--qa-style", "PASS", "--qa-body-style", "PASS",
        )
        with (self.generations / "GENERATION_MANIFEST.csv").open(encoding="utf-8-sig", newline="") as manifest_file:
            side_row = list(csv.DictReader(manifest_file))[-1]
        copied_side = Path(side_row["style_file"])
        self._cli(
            "task_execution_guard.py", "checkpoint", "--state", guard, "--event", "STAGE_COMPLETED",
            "--summary", "Synthetic side stage passed required QA.", "--stage", "03_PHYSIQUE_SIDE",
            "--evidence", copied_side, "--swimwear-rung", "EXTREME_MICRO", "--observed-topology", "EXTREME_MICRO",
        )

        back_prompt = "Create the canonical back view using the verified face, front, and side outputs."
        back_call = self._resolve_and_prepare_call(request_id, guard, plan_path, back_prompt, "04_PHYSIQUE_BACK")
        back_pack = next(binding for binding in back_call["targeted_pack_bindings"])
        back_manifest = json.loads(Path(back_pack["manifest_path"]).read_text(encoding="utf-8"))
        self.assertEqual([source["sha256"] for source in back_manifest["sources"]], [
            hashlib.sha256(copied_face.read_bytes()).hexdigest(),
            hashlib.sha256(copied_front.read_bytes()).hexdigest(),
            hashlib.sha256(copied_side.read_bytes()).hexdigest(),
        ])
        back_attempt = self._start_call(guard, plan_path, back_call)
        back_output = self.workspace / "back-stage.png"
        Image.new("RGB", (720, 1280), (143, 115, 101)).save(back_output)
        back_limb_evidence = limb_evidence.replace("view=FRONT;", "view=BACK;")
        self._cli(
            "style_pack_manager.py", "record-generation", "--workspace", self.workspace,
            "--style-name", self.style_name, "--image", back_output, "--request-id", request_id,
            "--description", "Synthetic back physique stage", "--fidelity", "70", "--status", "STAGING",
            "--character-id", "NEW", "--attempt-id", back_attempt, "--reference-plan", plan_path,
            "--stage-id", "04_PHYSIQUE_BACK", "--qa-attachments", "PASS", "--qa-canvas", "PASS",
            "--qa-stage-layer", "PASS", "--qa-face", "PASS", "--qa-body-silhouette", "PASS",
            "--qa-body-proportions", "PASS", "--qa-limb-proportions", "PASS", "--limb-qa-evidence", back_limb_evidence,
            "--qa-view", "PASS", "--qa-safe-coverage", "PASS", "--qa-clothing-topology", "PASS",
            "--qa-multiview-consistency", "PASS", "--qa-style", "PASS", "--qa-body-style", "PASS",
        )
        with (self.generations / "GENERATION_MANIFEST.csv").open(encoding="utf-8-sig", newline="") as manifest_file:
            back_row = list(csv.DictReader(manifest_file))[-1]
        copied_back = Path(back_row["style_file"])
        self._cli(
            "task_execution_guard.py", "checkpoint", "--state", guard, "--event", "STAGE_COMPLETED",
            "--summary", "Synthetic back stage passed required QA.", "--stage", "04_PHYSIQUE_BACK",
            "--evidence", copied_back, "--swimwear-rung", "EXTREME_MICRO", "--observed-topology", "EXTREME_MICRO",
        )

        assembly_prompt = "Assemble the verified face and all three physique views into a neutral 3/4 character image."
        assembly_call = self._resolve_and_prepare_call(
            request_id, guard, plan_path, assembly_prompt, "05_CHARACTER_ASSEMBLY",
        )
        self.assertEqual(len(assembly_call["stage_output_bindings"]), 4)
        assembly_attempt = self._start_call(guard, plan_path, assembly_call)
        assembly_output = self.workspace / "assembly-stage.png"
        Image.new("RGB", (720, 1280), (142, 114, 100)).save(assembly_output)
        self._cli(
            "style_pack_manager.py", "record-generation", "--workspace", self.workspace,
            "--style-name", self.style_name, "--image", assembly_output, "--request-id", request_id,
            "--description", "Synthetic canonical character assembly", "--fidelity", "70", "--status", "TEST",
            "--character-id", "NEW", "--attempt-id", assembly_attempt, "--reference-plan", plan_path,
            "--stage-id", "05_CHARACTER_ASSEMBLY", "--qa-attachments", "PASS", "--qa-canvas", "PASS",
            "--qa-stage-layer", "PASS", "--qa-face", "PASS", "--qa-body-silhouette", "PASS",
            "--qa-body-proportions", "PASS", "--qa-limb-proportions", "PASS", "--limb-qa-evidence", limb_evidence,
            "--qa-multiview-consistency", "PASS", "--qa-neutral-backdrop", "PASS", "--qa-style", "PASS",
            "--qa-body-style", "PASS",
        )
        assembly_row = None
        with (self.generations / "GENERATION_MANIFEST.csv").open(encoding="utf-8-sig", newline="") as manifest_file:
            assembly_row = list(csv.DictReader(manifest_file))[-1]
        copied_assembly = Path(assembly_row["style_file"])
        self._cli(
            "task_execution_guard.py", "checkpoint", "--state", guard, "--event", "STAGE_COMPLETED",
            "--summary", "Synthetic canonical assembly passed required QA.",
            "--stage", "05_CHARACTER_ASSEMBLY", "--evidence", copied_assembly,
        )
        self._cli(
            "task_execution_guard.py", "checkpoint", "--state", guard, "--event", "RESULT_DELIVERED",
            "--summary", "Synthetic assembly result delivery receipt.", "--attempt-id", assembly_attempt,
            "--delivery-evidence", str(copied_assembly),
        )
        self._cli(
            "task_execution_guard.py", "checkpoint", "--state", guard, "--event", "COMPLETE",
            "--summary", "All five canonical stages passed and the assembly was delivered.",
        )
        final_state = json.loads(guard.read_text(encoding="utf-8"))
        self.assertEqual(final_state["status"], "COMPLETE")
        self.assertEqual([row["id"] for row in final_state["required_stages"]], list(stages))
        self.assertEqual([row["status"] for row in final_state["required_stages"]], ["COMPLETED"] * 5)


if __name__ == "__main__":
    unittest.main()
