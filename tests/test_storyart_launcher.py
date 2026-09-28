"""Host-level tests for the bounded PowerShell StoryArt launcher."""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "scripts" / "storyart.ps1"


def powershell() -> str | None:
    return shutil.which("pwsh") or shutil.which("powershell")


@unittest.skipUnless(powershell(), "PowerShell is required for launcher tests")
class StoryArtLauncherTests(unittest.TestCase):
    def run_launcher(self, *args: str, env: dict[str, str] | None = None):
        return subprocess.run(
            [powershell(), "-NoProfile", "-NonInteractive", "-File", str(LAUNCHER), *args],
            cwd=ROOT,
            env=env,
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=30,
            check=False,
        )

    def test_uses_compatible_runtime_and_runs_read_only_help(self):
        result = self.run_launcher("style_pack_manager", "--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("usage:", result.stdout.lower())

    def test_launcher_preserves_unicode_menu_json(self):
        result = self.run_launcher(
            "style_pack_manager",
            "startup-menu-template",
            "--style-name",
            "RIOT_LOL_SPLASH",
            "--character-id",
            "CHAR_001",
            "--character-name",
            "Шанс",
            "--generation-purpose",
            "SCENE",
            "--json",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"title": "Стиль и референсы"', result.stdout)
        self.assertIn('Шанс (CHAR_001)', result.stdout)

if __name__ == "__main__":
    unittest.main()
