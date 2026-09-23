from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from tools.generation_call_contract import GenerationCallContractError, resolve_stage_slots


class GenerationCallContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.request_id = "REQ_TEST"
        self.source_a = self.root / "face.png"
        self.source_b = self.root / "front.png"
        Image.new("RGB", (160, 240), (180, 90, 70)).save(self.source_a)
        Image.new("RGB", (200, 300), (70, 120, 180)).save(self.source_b)
        self.bytes_a = self.source_a.read_bytes()
        self.bytes_b = self.source_b.read_bytes()
        self.hash_a = hashlib.sha256(self.bytes_a).hexdigest()
        self.hash_b = hashlib.sha256(self.bytes_b).hexdigest()
        self.plan = {
            "request_id": self.request_id,
            "generation_workflow": {
                "mode": "MULTI_STAGE",
                "stages": [
                    {
                        "stage_id": "01_FACE_IDENTITY",
                        "attachment_limit": 2,
                        "slots": [{"path": "source-face.png", "sha256": "a" * 64, "active_roles": ["FACE"], "slot": 1, "physically_attach": True}],
                    },
                    {
                        "stage_id": "02_PHYSIQUE_FRONT",
                        "attachment_limit": 2,
                        "slots": [
                            {"path": str(self.source_b), "sha256": self.hash_b, "active_roles": ["BODY"], "slot": 1},
                            {"path": "<STAGE_OUTPUT:01_FACE_IDENTITY>", "sha256": "STAGE_OUTPUT:01_FACE_IDENTITY", "stage_role": "FACE_IDENTITY_STAGE", "active_roles": ["FACE_IDENTITY_STAGE"], "slot": 2, "generated_stage_output": True},
                        ],
                    },
                    {
                        "stage_id": "03_PHYSIQUE_SIDE",
                        "attachment_limit": 2,
                        "slots": [
                            {"path": "<TARGETED_STAGE_PACK:01_FACE_IDENTITY+02_PHYSIQUE_FRONT>", "sha256": "TARGETED_STAGE_PACK:01_FACE_IDENTITY+02_PHYSIQUE_FRONT", "stage_role": "FACE_FRONT_TARGETED_PACK", "active_roles": ["FACE_FRONT_TARGETED_PACK"], "slot": 1, "generated_stage_output": True, "planned_targeted_pack": True, "targeted_pack_sources": ["01_FACE_IDENTITY", "02_PHYSIQUE_FRONT"]},
                            {"path": "style.png", "sha256": "b" * 64, "active_roles": ["STYLE"], "slot": 2},
                        ],
                    },
                ],
            },
        }

    def tearDown(self):
        self.temp.cleanup()

    def output(self, stage_id: str, path: Path, digest: str | None = None, **overrides):
        record = {
            "path": str(path),
            "sha256": digest or hashlib.sha256(path.read_bytes()).hexdigest(),
            "request_id": self.request_id,
            "stage_id": stage_id,
            "status": "STAGING",
            "qa_passed": True,
        }
        record.update(overrides)
        return record

    def valid_outputs(self):
        return {
            "01_FACE_IDENTITY": self.output("01_FACE_IDENTITY", self.source_a),
            "02_PHYSIQUE_FRONT": self.output("02_PHYSIQUE_FRONT", self.source_b),
        }

    def test_first_stage_accepts_future_placeholders_unchanged(self):
        result = resolve_stage_slots(self.plan, "01_FACE_IDENTITY", {}, self.root / "technical")
        self.assertEqual(result, self.plan["generation_workflow"]["stages"][0]["slots"])
        self.assertEqual(self.plan["generation_workflow"]["stages"][2]["slots"][0]["path"], "<TARGETED_STAGE_PACK:01_FACE_IDENTITY+02_PHYSIQUE_FRONT>")

    def test_stage_placeholder_resolves_to_checked_qa_output_and_preserves_role(self):
        outputs = {"01_FACE_IDENTITY": self.output("01_FACE_IDENTITY", self.source_a)}
        slots = resolve_stage_slots(self.plan, "02_PHYSIQUE_FRONT", outputs, self.root / "technical")
        self.assertEqual(slots[1]["path"], str(self.source_a.resolve()))
        self.assertEqual(slots[1]["sha256"], self.hash_a)
        self.assertEqual(slots[1]["active_roles"], ["FACE_IDENTITY_STAGE"])
        self.assertNotIn("generated_stage_output", slots[1])

    def test_targeted_pack_is_deterministic_and_preserves_sources(self):
        outputs = self.valid_outputs()
        output_dir = self.root / "technical"
        first = resolve_stage_slots(self.plan, "03_PHYSIQUE_SIDE", outputs, output_dir)[0]
        second = resolve_stage_slots(self.plan, "03_PHYSIQUE_SIDE", outputs, output_dir)[0]
        self.assertEqual(first, second)
        self.assertEqual(Path(first["path"]).read_bytes(), Path(second["path"]).read_bytes())
        self.assertEqual(self.source_a.read_bytes(), self.bytes_a)
        self.assertEqual(self.source_b.read_bytes(), self.bytes_b)
        self.assertEqual(hashlib.sha256(Path(first["path"]).read_bytes()).hexdigest(), first["sha256"])
        manifest = json.loads(Path(first["manifest_path"]).read_text(encoding="utf-8"))
        self.assertTrue(manifest["generator_safe"])
        self.assertEqual([item["sha256"] for item in manifest["sources"]], [self.hash_a, self.hash_b])
        self.assertTrue(all(Path(item["path"]).is_file() for item in manifest["sources"]))
        self.assertIn("MULTIVIEW_CONSISTENCY", manifest["scope"])

    def test_wrong_request_qa_rejection_mutation_and_missing_prior_output_fail(self):
        cases = [
            {"01_FACE_IDENTITY": self.output("01_FACE_IDENTITY", self.source_a, request_id="OTHER")},
            {"01_FACE_IDENTITY": self.output("01_FACE_IDENTITY", self.source_a, status="REJECTED")},
            {"01_FACE_IDENTITY": self.output("01_FACE_IDENTITY", self.source_a, qa_passed=False)},
            {"01_FACE_IDENTITY": self.output("01_FACE_IDENTITY", self.source_a, digest="0" * 64)},
            {},
        ]
        for outputs in cases:
            with self.subTest(outputs=outputs):
                with self.assertRaises(GenerationCallContractError):
                    resolve_stage_slots(self.plan, "02_PHYSIQUE_FRONT", outputs, self.root / "technical")

    def test_mutated_pack_source_fails_even_with_prior_stage_record(self):
        outputs = self.valid_outputs()
        self.source_b.write_bytes(b"changed after QA")
        with self.assertRaisesRegex(GenerationCallContractError, "bytes changed after QA"):
            resolve_stage_slots(self.plan, "03_PHYSIQUE_SIDE", outputs, self.root / "technical")

    def test_single_pass_slots_are_returned_exactly(self):
        single = {
            "request_id": self.request_id,
            "generation_workflow": {
                "mode": "SINGLE_PASS", "attachment_limit": 2,
                "slots": [{"path": "face.png", "sha256": "f" * 64, "slot": 1, "active_roles": ["FACE"]}],
            },
        }
        self.assertEqual(resolve_stage_slots(single, "ignored", {}, self.root), single["generation_workflow"]["slots"])

    def test_multistage_duplicates_deduplicate_by_hash_and_union_roles(self):
        plan = {
            "request_id": self.request_id,
            "generation_workflow": {
                "mode": "MULTI_STAGE",
                "stages": [{
                    "stage_id": "01_FACE_IDENTITY", "attachment_limit": 2,
                    "slots": [
                        {"path": str(self.source_a), "sha256": self.hash_a, "active_roles": ["FACE"], "slot": 1},
                        {"path": "same.png", "sha256": self.hash_a, "active_roles": ["FACE_IDENTITY_STAGE"], "slot": 2},
                    ],
                }],
            },
        }
        result = resolve_stage_slots(plan, "01_FACE_IDENTITY", {}, self.root)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["slot"], 1)
        self.assertEqual(result[0]["active_roles"], ["FACE", "FACE_IDENTITY_STAGE"])
        self.assertTrue(result[0]["physically_attach"])


if __name__ == "__main__":
    unittest.main()
