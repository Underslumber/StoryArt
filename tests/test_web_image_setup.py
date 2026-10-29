import json
import shutil
import tempfile
import unittest
from pathlib import Path

from tools import web_image_setup as setup


class PortableWebImageSetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_doctor_reports_setup_and_project_without_auth_probe(self):
        result = setup.doctor(self.root)
        self.assertEqual(result["authentication"], "NOT_CHECKED")
        self.assertEqual(result["runtime"], "SETUP_REQUIRED")
        self.assertEqual(result["prerequisites"], ["PROJECT", "LOGIN"])

    def test_full_installed_tree_and_node_are_integrity_pinned(self):
        installation = self.root / "installation"
        dependency = installation / "node_modules" / "fixture" / "index.js"
        dependency.parent.mkdir(parents=True)
        dependency.write_text("module.exports = 1", encoding="utf-8")
        config_dir = self.root / "config"
        config_dir.mkdir()
        node = shutil.which("node")
        self.assertTrue(node, "Node is required for portable runtime verification")
        manifest = self.root / "installation-manifest.json"
        files = [dependency, Path(node)]
        setup.write(manifest, {
            "version": 1,
            "dependency_tree_root": str((installation / "node_modules").resolve()),
            "dependency_tree_files": ["fixture/index.js"],
            "files": [{"path": str(file.resolve()), "sha256": setup.sha(file)} for file in files],
        })
        setup.write(config_dir / "chatgpt_web_image.runtime.local.json", {
            "installation": str(installation.resolve()),
            "node_path": str(Path(node).resolve()),
            "profile_dir": str((self.root / "profile").resolve()),
            "chrome_path": str(Path(node).resolve()),
            "manifest_path": str(manifest.resolve()),
            "manifest_sha256": setup.sha(manifest),
        })
        self.assertEqual(setup.verify(self.root)["installation"], str(installation.resolve()))
        dependency.write_text("module.exports = 2", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Runtime source/dependency changed"):
            setup.verify(self.root)

    def test_added_dependency_file_is_detected(self):
        installation = self.root / "installation"
        tree = installation / "node_modules"
        tree.mkdir(parents=True)
        config_dir = self.root / "config"
        config_dir.mkdir()
        node = shutil.which("node")
        manifest = self.root / "installation-manifest.json"
        setup.write(manifest, {
            "version": 1, "dependency_tree_root": str(tree.resolve()),
            "dependency_tree_files": [], "files": [{"path": str(Path(node).resolve()), "sha256": setup.sha(node)}],
        })
        setup.write(config_dir / "chatgpt_web_image.runtime.local.json", {
            "installation": str(installation.resolve()), "node_path": str(Path(node).resolve()),
            "profile_dir": str((self.root / "profile").resolve()), "chrome_path": str(Path(node).resolve()),
            "manifest_path": str(manifest.resolve()), "manifest_sha256": setup.sha(manifest),
        })
        (tree / "unexpected.js").write_text("unexpected", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "dependency tree contents changed"):
            setup.verify(self.root)

    def test_project_binding_and_login_status_do_not_claim_authentication(self):
        config_dir = self.root / "config"
        config_dir.mkdir()
        project = config_dir / "chatgpt_web_image.local.json"
        setup.bind_project(self.root, "https://chatgpt.com/g/g-p-abc123/project", "Portable Web")
        route = json.loads((config_dir / "chatgpt_web_image.local.json").read_text())
        self.assertEqual(route["project_id"], "g-p-abc123")
        self.assertEqual(setup.doctor(self.root)["authentication"], "NOT_CHECKED")


if __name__ == "__main__":
    unittest.main()
