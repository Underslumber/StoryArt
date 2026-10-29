import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from tools.export_web_image_kit import export


class PortableExportTests(unittest.TestCase):
    def temporary_workspace(self):
        base = Path(__file__).resolve().parents[1] / ".agent" / "portable-web-image" / "export-tests"
        base.mkdir(parents=True, exist_ok=True)
        return tempfile.TemporaryDirectory(dir=base)

    def test_source_export_omits_private_tracked_data(self):
        with self.temporary_workspace() as directory:
            root = Path(directory)
            names = ["scripts/setup-web-image.ps1", "tools/web_image_setup.py",
                     "runtime/chatgpt-web-image/package-lock.json", ".codex/agents/web_image.toml",
                     "config/chatgpt_web_image.local.json", "config/chatgpt_web_image.runtime.local.json",
                     ".codex/config.toml", "AGENTS.md", "runtime/chatgpt-web-image/profile/Cookies",
                     "runtime/chatgpt-web-image/broker/broker.exe", "docs/GENERATION_RULES.md"]
            # Profile directories must never become part of a source kit even
            # if someone accidentally tracked them.
            for name in names:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("fixture", encoding="utf-8")
            result = type("Listing", (), {"stdout": ("\0".join(names) + "\0").encode()})()
            output = root / "kit.zip"
            with patch("tools.export_web_image_kit.subprocess.run", return_value=result):
                export(root, output)
            with zipfile.ZipFile(output) as archive:
                files = set(archive.namelist())
                self.assertNotIn("runtime/chatgpt-web-image/profile/Cookies", files)
                self.assertNotIn("config/chatgpt_web_image.local.json", files)
                self.assertNotIn("config/chatgpt_web_image.runtime.local.json", files)
                self.assertNotIn(".codex/config.toml", files)
                self.assertNotIn("AGENTS.md", files)
                self.assertNotIn("docs/GENERATION_RULES.md", files)
                self.assertNotIn("runtime/chatgpt-web-image/broker/broker.exe", files)
                manifest = json.loads(archive.read("PORTABLE_MANIFEST.json"))
                self.assertEqual(len(manifest["files"]), 4)

    def test_incomplete_kit_is_never_written(self):
        with self.temporary_workspace() as directory:
            root = Path(directory)
            output = root / "kit.zip"
            result = type("Listing", (), {"stdout": b"README.md\0"})()
            with patch("tools.export_web_image_kit.subprocess.run", return_value=result):
                with self.assertRaises(ValueError):
                    export(root, output)
            self.assertFalse(output.exists())
