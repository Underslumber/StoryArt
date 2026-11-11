"""Request rendering support stays request-bound at the execution boundary."""
from argparse import Namespace
from pathlib import Path
import tempfile
import unittest

from tools import style_pack_manager as manager
from tools import task_execution_guard as guard


class StyleScopeGuardTests(unittest.TestCase):
    def test_request_support_passes_only_for_its_exact_request(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = manager.make_paths(Path(directory), "Fixture")
            manager.command_init(Namespace(workspace=Path(directory), style_name="Fixture", source="", non_recursive=False))
            relative = Path("01_WORK/STYLE_CROPS/MASTER_STYLE_request.png")
            file = paths.pack / relative
            file.write_bytes(b"synthetic rendering support")
            row = {"reference_id":"REF_1", "filename":file.name, "stored_relative_path":relative.as_posix(),
                   "primary_role":"MASTER_STYLE", "reference_scope":"REQUEST", "request_id":"REQ_A",
                   "status":"TEST", "generator_safe":"YES"}
            manager.write_reference_manifests(paths, [row])
            plan = {"pack_path":str(paths.pack), "style_name":"Fixture", "request_id":"REQ_A",
                    "selected_references":{"style":[{"path":str(file), "status":"REQUEST_LOCAL_STYLE_CANDIDATE",
                    "reference_scope":"CURRENT_REQUEST_ONLY", "permanent_anchor":False}]}}
            slots = [{"path":str(file), "active_roles":["STYLE"]}]
            guard._validate_request_local_style_manifest(plan, slots)
            plan["request_id"] = "REQ_B"
            with self.assertRaisesRegex(guard.GuardError, "no longer positive"):
                guard._validate_request_local_style_manifest(plan, slots)
            plan["request_id"] = ""
            with self.assertRaises(guard.GuardError):
                guard._validate_request_local_style_manifest(plan, slots)
