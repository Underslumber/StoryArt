#!/usr/bin/env python3
"""Install optional reference pools from their original publishers.

Git carries each pool's catalog (`reference_packs/<POOL>/`): source terms,
per-file SHA-256 and download URL, plus the metadata StoryArt reads. Image
files never enter Git; `install` fetches them from the publisher (or copies
them from a local folder) and accepts a file only when its hash matches.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import sys
import tempfile
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
PACKS_ROOT = ROOT / "reference_packs"
FILES_NAME = "files.csv"
METADATA_DIR = "metadata"
FILE_FIELDS = ["path", "sha256", "size", "url"]
USER_AGENT = "StoryArt-reference-packs/1"
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


class PackError(ValueError):
    pass


@dataclass(frozen=True)
class PackFile:
    path: str
    sha256: str
    size: int
    url: str


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def safe_join(root: Path, relative: str) -> Path:
    candidate = Path(relative.replace("\\", "/"))
    if candidate.is_absolute() or ".." in candidate.parts or not candidate.parts:
        raise PackError(f"Unsafe relative path: {relative}")
    resolved = (root / candidate).resolve()
    # Windows may return an extended-length (\\?\) form for one side only.
    strip = lambda p: Path(str(p)[4:]) if str(p).startswith("\\\\?\\") else p
    try:
        strip(resolved).relative_to(strip(root.resolve()))
    except ValueError:
        raise PackError(f"Path escapes the pool root: {relative}") from None
    return strip(resolved)


def pack_dir(pool: str, packs_root: Path = PACKS_ROOT) -> Path:
    return safe_join(packs_root, pool)


def load_pack(pool: str, packs_root: Path = PACKS_ROOT) -> dict[str, Any]:
    path = pack_dir(pool, packs_root) / "pack.json"
    if not path.is_file():
        raise PackError(f"Unknown reference pack: {pool}")
    pack = json.loads(path.read_text(encoding="utf-8"))
    for key in ("pool", "target_root", "source"):
        if key not in pack:
            raise PackError(f"{path} is missing {key}")
    if pack["pool"] != pool:
        raise PackError(f"{path} declares pool {pack['pool']}, expected {pool}")
    return pack


def available_pools(packs_root: Path = PACKS_ROOT) -> list[str]:
    if not packs_root.is_dir():
        return []
    return sorted(p.name for p in packs_root.iterdir() if (p / "pack.json").is_file())


def load_files(pool: str, packs_root: Path = PACKS_ROOT) -> list[PackFile]:
    path = pack_dir(pool, packs_root) / FILES_NAME
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    files = [PackFile(r["path"], r["sha256"].lower(), int(r["size"]), r.get("url", "")) for r in rows]
    seen: set[str] = set()
    for item in files:
        if item.path in seen:
            raise PackError(f"{pool}: duplicate path {item.path}")
        seen.add(item.path)
    return files


def write_files(pool: str, files: Iterable[PackFile], packs_root: Path = PACKS_ROOT) -> None:
    path = pack_dir(pool, packs_root) / FILES_NAME
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(FILE_FIELDS)
        for item in sorted(files, key=lambda f: f.path):
            writer.writerow([item.path, item.sha256, item.size, item.url])


def target_root(pack: dict[str, Any], workspace: Path) -> Path:
    return safe_join(workspace, pack["target_root"])


def file_state(item: PackFile, root: Path, deep: bool) -> str:
    path = safe_join(root, item.path)
    if not path.is_file():
        return "MISSING"
    if path.stat().st_size != item.size:
        return "CHANGED"
    if deep and sha256_file(path) != item.sha256:
        return "CHANGED"
    return "OK"


def pool_status(pool: str, workspace: Path, packs_root: Path = PACKS_ROOT, deep: bool = False) -> dict[str, Any]:
    pack = load_pack(pool, packs_root)
    files = load_files(pool, packs_root)
    root = target_root(pack, workspace)
    counts = {"OK": 0, "MISSING": 0, "CHANGED": 0}
    for item in files:
        counts[file_state(item, root, deep)] += 1
    if not files:
        status = "CATALOG_EMPTY"
    elif counts["OK"] == len(files):
        status = "INSTALLED"
    elif counts["OK"] == 0 and counts["CHANGED"] == 0:
        status = "NOT_INSTALLED"
    else:
        status = "PARTIAL" if counts["CHANGED"] == 0 else "STALE"
    return {
        "pool": pool,
        "title": pack.get("title", pool),
        "status": status,
        "files": len(files),
        "bytes": sum(f.size for f in files),
        "present": counts["OK"],
        "missing": counts["MISSING"],
        "changed": counts["CHANGED"],
        "target_root": pack["target_root"],
        "redistribution": pack["source"].get("redistribution", "unknown"),
    }


def fetch(url: str, destination: Path, timeout: float, attempts: int = 3) -> None:
    if not url.startswith("https://"):
        raise PackError(f"Only https downloads are allowed: {url}")
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=timeout) as response, destination.open("wb") as out:
                shutil.copyfileobj(response, out)
            return
        except OSError as error:
            last_error = error
            time.sleep(2 ** attempt)
    raise PackError(f"Download failed after {attempts} attempts: {url}: {last_error}")


def local_candidate(item: PackFile, from_dir: Path) -> Path | None:
    for candidate in (from_dir / item.path, from_dir / Path(item.path).name):
        if candidate.is_file() and candidate.stat().st_size == item.size and sha256_file(candidate) == item.sha256:
            return candidate
    return None


def install_file(item: PackFile, root: Path, from_dir: Path | None, timeout: float) -> str:
    destination = safe_join(root, item.path)
    if destination.is_file() and sha256_file(destination) == item.sha256:
        return "KEPT"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if from_dir is not None:
        source = local_candidate(item, from_dir)
        if source is not None:
            shutil.copyfile(source, destination)
            return "COPIED"
    if not item.url:
        return "NO_SOURCE"
    with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".download-", delete=False) as handle:
        temporary = Path(handle.name)
    try:
        try:
            fetch(item.url, temporary, timeout)
        except PackError:
            return "DOWNLOAD_FAILED"
        if sha256_file(temporary) != item.sha256:
            return "HASH_MISMATCH"
        temporary.replace(destination)
        return "DOWNLOADED"
    finally:
        temporary.unlink(missing_ok=True)


def install_metadata(pool: str, pack: dict[str, Any], root: Path, packs_root: Path, overwrite: bool) -> list[str]:
    notes = []
    source_dir = pack_dir(pool, packs_root) / METADATA_DIR
    for name in pack.get("metadata", []):
        source = safe_join(source_dir, name)
        destination = safe_join(root, name)
        if destination.is_file() and destination.read_bytes() != source.read_bytes() and not overwrite:
            notes.append(f"kept local {name}; it differs from the catalog (use --overwrite-metadata to replace)")
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    return notes


def install(pool: str, workspace: Path, packs_root: Path = PACKS_ROOT, from_dir: Path | None = None,
            workers: int = 4, timeout: float = 60.0, overwrite_metadata: bool = False) -> dict[str, Any]:
    pack = load_pack(pool, packs_root)
    files = load_files(pool, packs_root)
    if not files:
        raise PackError(f"{pool}: catalog has no files yet; run build on a machine that has the pool")
    root = target_root(pack, workspace)
    root.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool_executor:
        outcomes = list(pool_executor.map(lambda f: (f.path, install_file(f, root, from_dir, timeout)), files))
    summary: dict[str, int] = {}
    failed = []
    for path, outcome in outcomes:
        summary[outcome] = summary.get(outcome, 0) + 1
        if outcome in {"NO_SOURCE", "HASH_MISMATCH", "DOWNLOAD_FAILED"}:
            failed.append({"path": path, "outcome": outcome})
    notes = install_metadata(pool, pack, root, packs_root, overwrite_metadata)
    return {"pool": pool, "target_root": pack["target_root"], "summary": summary,
            "failed": failed[:50], "failed_count": len(failed), "notes": notes,
            "status": "INSTALLED" if not failed else "INCOMPLETE"}


def read_records(path: Path, records_key: str | None) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))
    data = json.loads(path.read_text(encoding="utf-8"))
    return data[records_key] if records_key else data


def resolve_local(relative: str, root: Path, workspace: Path) -> Path | None:
    """Manifests record paths relative to either the pool root or the workspace."""
    for base in (root, workspace):
        try:
            candidate = safe_join(base, relative)
        except (PackError, ValueError):
            continue
        if candidate.is_file():
            return candidate
    return None


def build_paths(spec: dict[str, Any], root: Path, workspace: Path) -> list[Path]:
    if "glob" in spec:
        return [p for p in sorted(root.glob(spec["glob"])) if p.is_file()]
    listing = spec["paths_from"]
    paths = set()
    for record in read_records(safe_join(root, listing["file"]), listing.get("records")):
        local = resolve_local(record[listing["path"]], root, workspace)
        if local is not None:
            paths.add(local)
    return sorted(paths)


def build_url_map(spec: dict[str, Any], root: Path, workspace: Path) -> dict[str, str]:
    """Map sha256 -> original URL from the pool's download manifests.

    Files are matched by hash, so crops and other derivatives never inherit the
    URL of the original they were cut from.
    """
    urls: dict[str, str] = {}
    for source in spec.get("url_sources", []):
        path = resolve_local(source["file"], root, workspace)
        if path is None:
            continue
        excluded = source.get("exclude_url_substrings", [])
        for record in read_records(path, source.get("records")):
            url = record.get(source["url"]) or ""
            if not url.startswith("https://") or any(part in url for part in excluded):
                continue
            digest = (record.get(source["sha256"]) or "").lower() if "sha256" in source else ""
            if not digest and "path" in source:
                local = resolve_local(record.get(source["path"]) or "", root, workspace)
                digest = sha256_file(local) if local else ""
            if digest:
                urls.setdefault(digest, url)
    return urls


def relativize(data: bytes, workspace: Path) -> bytes:
    """Strip this machine's workspace prefix from metadata before it enters Git."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return data
    base = str(workspace).rstrip("\\/")
    variants = {base, base.replace("\\", "/"), base.replace("\\", "\\\\"), base.replace("/", "\\")}
    for prefix in sorted(variants, key=len, reverse=True):
        for separator in ("\\\\", "\\", "/"):
            text = text.replace(prefix + separator, "")
        text = text.replace(prefix, ".")
    return text.encode("utf-8")


def build(pool: str, workspace: Path, packs_root: Path = PACKS_ROOT) -> dict[str, Any]:
    """Regenerate files.csv and metadata from a machine that already holds the pool."""
    pack = load_pack(pool, packs_root)
    spec = pack.get("build")
    if not spec:
        raise PackError(f"{pool}: pack.json has no build section")
    root = target_root(pack, workspace)
    template = spec.get("url_template", "")
    url_map = build_url_map(spec, root, workspace)
    files = []
    for path in build_paths(spec, root, workspace):
        relative = path.relative_to(root).as_posix()
        digest = sha256_file(path)
        url = url_map.get(digest) or (template.format(name=path.name, stem=path.stem, path=relative) if template else "")
        files.append(PackFile(relative, digest, path.stat().st_size, url))
    if not files:
        raise PackError(f"{pool}: no files found for build spec under {pack['target_root']}")
    write_files(pool, files, packs_root)
    metadata_dir = pack_dir(pool, packs_root) / METADATA_DIR
    copied = []
    for name in pack.get("metadata", []):
        source = safe_join(root, name)
        if source.is_file():
            destination = safe_join(metadata_dir, name)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(relativize(source.read_bytes(), workspace))
            copied.append(name)
    return {"pool": pool, "files": len(files), "bytes": sum(f.size for f in files),
            "with_url": sum(1 for f in files if f.url), "metadata": copied}


def describe_terms(pack: dict[str, Any]) -> str:
    source = pack["source"]
    lines = [f"{pack.get('title', pack['pool'])}: {source.get('name', '')}"]
    for key in ("author", "homepage", "terms_url"):
        if source.get(key):
            lines.append(f"  {key}: {source[key]}")
    if source.get("terms_summary"):
        lines.append(f"  terms: {source['terms_summary']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=ROOT)
    parser.add_argument("--packs-root", type=Path, default=PACKS_ROOT)
    commands = parser.add_subparsers(dest="command", required=True)
    listing = commands.add_parser("list", help="show packs and whether they are installed")
    listing.add_argument("--json", action="store_true")
    verify = commands.add_parser("verify", help="hash-check installed files")
    verify.add_argument("pools", nargs="*")
    setup = commands.add_parser("install", help="download or copy pack files and metadata")
    setup.add_argument("pools", nargs="*", help="pack names; omit with --all")
    setup.add_argument("--all", action="store_true")
    setup.add_argument("--from-dir", type=Path, help="copy matching files from a local folder before downloading")
    setup.add_argument("--workers", type=int, default=4)
    setup.add_argument("--timeout", type=float, default=60.0)
    setup.add_argument("--overwrite-metadata", action="store_true")
    rebuild = commands.add_parser("build", help="regenerate a catalog from the installed pool")
    rebuild.add_argument("pool")
    args = parser.parse_args(argv)
    workspace = args.workspace.resolve()
    packs_root = args.packs_root.resolve()
    try:
        if args.command == "list":
            rows = [pool_status(p, workspace, packs_root) for p in available_pools(packs_root)]
            if args.json:
                print(json.dumps(rows, ensure_ascii=False, indent=2))
            else:
                for row in rows:
                    print(f"{row['pool']}: {row['status']} {row['present']}/{row['files']} files, "
                          f"{row['bytes'] / 1e6:.0f} MB -> {row['target_root']}")
            return 0
        if args.command == "verify":
            rows = [pool_status(p, workspace, packs_root, deep=True) for p in (args.pools or available_pools(packs_root))]
            print(json.dumps(rows, ensure_ascii=False, indent=2))
            return 0 if all(r["status"] == "INSTALLED" for r in rows) else 1
        if args.command == "install":
            pools = available_pools(packs_root) if args.all else args.pools
            if not pools:
                raise PackError("Name at least one pack or pass --all")
            results = []
            for pool in pools:
                print(describe_terms(load_pack(pool, packs_root)), file=sys.stderr)
                results.append(install(pool, workspace, packs_root, args.from_dir, args.workers,
                                       args.timeout, args.overwrite_metadata))
            print(json.dumps(results, ensure_ascii=False, indent=2))
            return 0 if all(r["status"] == "INSTALLED" for r in results) else 1
        print(json.dumps(build(args.pool, workspace, packs_root), ensure_ascii=False, indent=2))
        return 0
    except PackError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
