"""Export allowlisted portable StoryArt sources without machine/account data."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile


def export(workspace: Path, output: Path) -> dict:
    workspace = workspace.resolve()
    listing = subprocess.run(
        ["git", "-c", "core.fsmonitor=false", "ls-files", "-co", "--exclude-standard", "-z"],
        cwd=workspace, check=True, stdout=subprocess.PIPE,
    ).stdout.decode("utf-8").split("\0")
    roots = {"scripts", "tools", "config", "docs", "templates", "tests", "skills", "runtime"}
    top = {".gitignore", "README.md", "requirements.txt", "requirements-dev.txt", "AGENTS.example.md"}
    forbidden = {"docs/GENERATION_RULES.md", "docs/PERFORMANCE_AUDIT_2026-09-09.md"}
    entries = []
    for name in sorted(set(filter(None, listing))):
        rel = Path(name)
        if name not in top and rel.parts[0] not in roots and name != ".codex/agents/web_image.toml":
            continue
        if name in forbidden or any(part.lower() in {"node_modules", "__pycache__", ".local", ".agent", ".venv", "profile", "profiles", "sessions", "oracle-home", "downloads"} for part in rel.parts):
            continue
        if ".local." in rel.name or rel.suffix.lower() in {".exe", ".dll", ".png", ".jpg", ".jpeg", ".webp", ".zip", ".pyc"}:
            continue
        source = workspace / rel
        if not source.is_file():
            continue
        if source.is_symlink() or not source.resolve().is_relative_to(workspace):
            raise ValueError(f"External or symbolic source rejected: {name}")
        payload = source.read_bytes()
        entries.append((name, payload, hashlib.sha256(payload).hexdigest()))
    required = {"scripts/setup-web-image.ps1", "tools/web_image_setup.py", "runtime/chatgpt-web-image/package-lock.json", ".codex/agents/web_image.toml"}
    missing = required - {name for name, _, _ in entries}
    if missing:
        raise ValueError(f"Portable implementation incomplete: {sorted(missing)}")
    manifest = {"schema_version": 1, "files": [{"path": name, "sha256": sha, "bytes": len(payload)} for name, payload, sha in entries]}
    output = output.resolve()
    if output.exists():
        raise FileExistsError(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload, _ in entries:
            archive.writestr(name, payload)
        archive.writestr("PORTABLE_MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    return {"path": str(output), "files": len(entries), "sha256": hashlib.sha256(output.read_bytes()).hexdigest()}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(export(args.workspace, args.output), ensure_ascii=False, indent=2))
