import csv
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from tools import reference_packs as packs


def write_pack(packs_root: Path, pool: str, files: list[tuple[str, bytes, str]], metadata: dict[str, str] | None = None) -> None:
    pack = packs_root / pool
    (pack / "metadata").mkdir(parents=True)
    (pack / "pack.json").write_text(json.dumps({
        "schema_version": 1, "pool": pool, "target_root": f"LIBRARY/{pool}",
        "source": {"name": "test", "redistribution": "forbidden"},
        "metadata": sorted(metadata or {}),
        "build": {"glob": "img/*.png", "url_template": "https://example.invalid/{name}"},
    }), encoding="utf-8")
    for name, text in (metadata or {}).items():
        (pack / "metadata" / name).write_text(text, encoding="utf-8")
    with (pack / "files.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(packs.FILE_FIELDS)
        for path, data, url in files:
            writer.writerow([path, hashlib.sha256(data).hexdigest(), len(data), url])


class ReferencePackTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.workspace = self.base / "workspace"
        self.packs_root = self.base / "packs"
        self.backup = self.base / "backup"
        self.workspace.mkdir()
        self.backup.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_install_copies_hash_matching_local_files_and_metadata(self) -> None:
        write_pack(self.packs_root, "DEMO", [("img/a.png", b"alpha", ""), ("img/b.png", b"beta", "")],
                   {"INDEX.json": "{}"})
        (self.backup / "a.png").write_bytes(b"alpha")
        (self.backup / "img").mkdir()
        (self.backup / "img" / "b.png").write_bytes(b"beta")

        result = packs.install("DEMO", self.workspace, self.packs_root, from_dir=self.backup)

        self.assertEqual(result["status"], "INSTALLED")
        self.assertEqual(result["summary"], {"COPIED": 2})
        root = self.workspace / "LIBRARY" / "DEMO"
        self.assertEqual((root / "img" / "a.png").read_bytes(), b"alpha")
        self.assertEqual((root / "INDEX.json").read_text(encoding="utf-8"), "{}")
        self.assertEqual(packs.pool_status("DEMO", self.workspace, self.packs_root, deep=True)["status"], "INSTALLED")

    def test_install_rejects_wrong_bytes_and_reports_missing_source(self) -> None:
        write_pack(self.packs_root, "DEMO", [("img/a.png", b"alpha", "")])
        (self.backup / "a.png").write_bytes(b"tampered")

        result = packs.install("DEMO", self.workspace, self.packs_root, from_dir=self.backup)

        self.assertEqual(result["status"], "INCOMPLETE")
        self.assertEqual(result["failed"], [{"path": "img/a.png", "outcome": "NO_SOURCE"}])
        self.assertFalse((self.workspace / "LIBRARY" / "DEMO" / "img" / "a.png").exists())

    def test_installed_files_are_kept_and_changed_local_metadata_is_not_overwritten(self) -> None:
        write_pack(self.packs_root, "DEMO", [("img/a.png", b"alpha", "")], {"INDEX.json": "{\"v\": 2}"})
        root = self.workspace / "LIBRARY" / "DEMO"
        (root / "img").mkdir(parents=True)
        (root / "img" / "a.png").write_bytes(b"alpha")
        (root / "INDEX.json").write_text("{\"local\": true}", encoding="utf-8")

        result = packs.install("DEMO", self.workspace, self.packs_root)

        self.assertEqual(result["summary"], {"KEPT": 1})
        self.assertEqual(len(result["notes"]), 1)
        self.assertEqual((root / "INDEX.json").read_text(encoding="utf-8"), "{\"local\": true}")

    def test_status_distinguishes_missing_and_changed_files(self) -> None:
        write_pack(self.packs_root, "DEMO", [("img/a.png", b"alpha", ""), ("img/b.png", b"beta", "")])
        self.assertEqual(packs.pool_status("DEMO", self.workspace, self.packs_root)["status"], "NOT_INSTALLED")
        root = self.workspace / "LIBRARY" / "DEMO" / "img"
        root.mkdir(parents=True)
        (root / "a.png").write_bytes(b"alpha")
        self.assertEqual(packs.pool_status("DEMO", self.workspace, self.packs_root)["status"], "PARTIAL")
        (root / "b.png").write_bytes(b"BETA")
        self.assertEqual(packs.pool_status("DEMO", self.workspace, self.packs_root, deep=True)["status"], "STALE")

    def test_build_writes_catalog_with_urls_and_metadata(self) -> None:
        write_pack(self.packs_root, "DEMO", [], {"INDEX.json": "old"})
        (self.packs_root / "DEMO" / "files.csv").unlink()
        root = self.workspace / "LIBRARY" / "DEMO"
        (root / "img").mkdir(parents=True)
        (root / "img" / "a.png").write_bytes(b"alpha")
        (root / "INDEX.json").write_text("new", encoding="utf-8")

        result = packs.build("DEMO", self.workspace, self.packs_root)

        self.assertEqual(result["files"], 1)
        files = packs.load_files("DEMO", self.packs_root)
        self.assertEqual(files[0].path, "img/a.png")
        self.assertEqual(files[0].url, "https://example.invalid/a.png")
        self.assertEqual((self.packs_root / "DEMO" / "metadata" / "INDEX.json").read_text(encoding="utf-8"), "new")

    def test_paths_cannot_escape_the_pool_root(self) -> None:
        write_pack(self.packs_root, "DEMO", [("../outside.png", b"x", "")])
        with self.assertRaises(packs.PackError):
            packs.install("DEMO", self.workspace, self.packs_root, from_dir=self.backup)

    def test_only_https_downloads_are_allowed(self) -> None:
        with self.assertRaises(packs.PackError):
            packs.fetch("http://example.invalid/a.png", self.base / "a.png", timeout=1)

    def test_shipped_catalogs_are_consistent(self) -> None:
        for pool in packs.available_pools():
            pack = packs.load_pack(pool)
            files = packs.load_files(pool)
            for name in pack.get("metadata", []) if files else []:
                self.assertTrue((packs.pack_dir(pool) / "metadata" / name).is_file(), f"{pool}: {name}")
            for item in files:
                self.assertRegex(item.sha256, r"^[0-9a-f]{64}$")
                self.assertTrue(item.url == "" or item.url.startswith("https://"), item.path)
            for name in pack.get("metadata", []) if files else []:
                text = (packs.pack_dir(pool) / "metadata" / name).read_text(encoding="utf-8")
                self.assertNotRegex(text, r"(?i)\b[a-z]:[\\/]+(users|projects)\b", f"{pool}: {name}")

    def test_build_maps_urls_by_hash_and_reads_paths_from_manifest(self) -> None:
        write_pack(self.packs_root, "DEMO", [], {"ITEMS.json": ""})
        pack_file = self.packs_root / "DEMO" / "pack.json"
        pack = json.loads(pack_file.read_text(encoding="utf-8"))
        pack["build"] = {
            "paths_from": {"file": "ITEMS.json", "records": "items", "path": "local_path"},
            "url_sources": [{"file": "downloads.csv", "path": "local_path", "url": "url",
                             "exclude_url_substrings": ["token="]}],
        }
        pack_file.write_text(json.dumps(pack), encoding="utf-8")
        root = self.workspace / "LIBRARY" / "DEMO"
        (root / "raw").mkdir(parents=True)
        (root / "crops").mkdir()
        (root / "raw" / "a.png").write_bytes(b"alpha")
        (root / "raw" / "b.png").write_bytes(b"beta")
        (root / "crops" / "a.png").write_bytes(b"alpha")
        (root / "crops" / "b_crop.png").write_bytes(b"be")
        (root / "crops" / "c.png").write_bytes(b"gamma")
        (root / "downloads.csv").write_text(
            "local_path,url\n"
            "LIBRARY\\DEMO\\raw\\a.png,https://example.invalid/a.png\n"
            "raw/b.png,https://example.invalid/b.png\n"
            "crops/c.png,https://example.invalid/c.png?token=x\n", encoding="utf-8")
        (root / "ITEMS.json").write_text(json.dumps({"items": [
            {"local_path": "crops\\a.png"}, {"local_path": "crops/b_crop.png"}, {"local_path": "crops/c.png"},
            {"local_path": f"{self.workspace}\\gone.png"}]}), encoding="utf-8")

        result = packs.build("DEMO", self.workspace, self.packs_root)

        urls = {f.path: f.url for f in packs.load_files("DEMO", self.packs_root)}
        self.assertEqual(urls, {"crops/a.png": "https://example.invalid/a.png", "crops/b_crop.png": "", "crops/c.png": ""})
        self.assertEqual(result["with_url"], 1)
        metadata = (self.packs_root / "DEMO" / "metadata" / "ITEMS.json").read_text(encoding="utf-8")
        self.assertNotIn(str(self.workspace).replace("\\", "\\\\"), metadata)
        self.assertIn("gone.png", metadata)

    def test_failed_download_is_reported_not_raised(self) -> None:
        write_pack(self.packs_root, "DEMO", [("img/a.png", b"alpha", "https://example.invalid/a.png")])
        original = packs.fetch

        def failing_fetch(url: str, destination: Path, timeout: float, attempts: int = 3) -> None:
            raise packs.PackError("offline")

        packs.fetch = failing_fetch
        try:
            result = packs.install("DEMO", self.workspace, self.packs_root)
        finally:
            packs.fetch = original
        self.assertEqual(result["status"], "INCOMPLETE")
        self.assertEqual(result["failed"], [{"path": "img/a.png", "outcome": "DOWNLOAD_FAILED"}])

    def test_pose_atlas_catalog_matches_its_group_metadata(self) -> None:
        files = {f.path: f.sha256 for f in packs.load_files("POSE_ATLAS")}
        groups = json.loads((packs.pack_dir("POSE_ATLAS") / "metadata" / "POSE_GROUPS.json").read_text(encoding="utf-8"))
        views = {v["relative_path"]: v["sha256"] for g in groups["groups"] for v in g["views"]}
        self.assertEqual(len(files), 1439)
        self.assertEqual(files, views)


if __name__ == "__main__":
    unittest.main()
