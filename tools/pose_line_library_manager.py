#!/usr/bin/env python3
"""Build and validate a local pose-line reference library.

The manager is intentionally conservative:

* downloaded images stay outside Git in ``POSE_LINE_REFERENCE_LIBRARY``;
* every candidate keeps its search page, direct URL, hash, and rights status;
* pixel heuristics only prepare visual-review sheets and never approve an image;
* a complete human/AI review CSV is required before curated files are created.

The resulting library is a soft POSE aid.  It is not a BODY, style, face,
clothing, or physiology authority.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import re
import shutil
import urllib.parse
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps, ImageStat


WORKSPACE = Path(__file__).resolve().parents[1]
LIBRARY_NAME = "POSE_LINE_REFERENCE_LIBRARY"
MANIFEST_NAME = "POSE_LINE_CANDIDATES.csv"
FINAL_MANIFEST_NAME = "POSE_LINE_MANIFEST.csv"
REVIEW_NAME = "POSE_LINE_REVIEW.csv"
CACHE_NAME = "SEARCH_CACHE.json"
REPORT_NAME = "COLLECTION_REPORT.json"

DEFAULT_QUERIES = (
    "скетч женского тела контуры позы",
    "скетч обнаженного женского тела контуры",
    "female body outline pose sketch",
    "female figure line art pose reference",
    "female anatomy outline drawing poses",
    "woman body contour drawing",
    "female fashion croquis body outline poses",
    "adult woman body lineart reference",
)

RESULT_PATTERN = re.compile(
    r"&quot;alt&quot;:&quot;(.*?)&quot;.*?"
    r"&quot;origUrl&quot;:&quot;(.*?)&quot;.*?"
    r"&quot;snippet&quot;:\{.*?&quot;url&quot;:&quot;(.*?)&quot;",
    re.S,
)
EXCLUDED_METADATA = re.compile(
    r"\b(child|children|kid|kids|baby|infant|teen|teenager|boy|boys|"
    r"schoolgirl|schoolboy|porn|hentai|sexual|explicit|genitals|vulva|penis)\b",
    re.I,
)
SUPPORTED_FORMATS = {"JPEG": ".jpg", "JPG": ".jpg", "PNG": ".png", "WEBP": ".webp", "GIF": ".gif"}
REVIEW_DECISIONS = {"PRIMARY_POSE_LINE", "POSE_OR_DETAIL", "STYLIZED_ONLY", "REJECTED"}
USABLE_DECISIONS = REVIEW_DECISIONS - {"REJECTED"}
USER_AGENT = "Mozilla/5.0 (compatible; StoryArt pose-line local reference collector)"

CANDIDATE_FIELDS = (
    "candidate_id", "alt", "original_url", "page_url", "matched_queries",
    "rights_status", "publication_status", "qa_status", "qa_notes",
    "local_path", "sha256", "width", "height", "format",
    "light_fraction", "dark_fraction", "mean_chroma", "edge_mean",
)


class PoseLineError(RuntimeError):
    pass


def paths(workspace: Path) -> dict[str, Path]:
    root = workspace.resolve() / LIBRARY_NAME
    legacy_root = (
        workspace.resolve()
        / "BODY_REFERENCE_LIBRARY"
        / "02_LOCAL_ONLY"
        / "ANATOMY_CONTOUR_REFERENCE_LIBRARY"
        / "08_DOWNLOADED_LINE_ART"
    )
    return {
        "root": root,
        "incoming": root / "00_INCOMING",
        "sheets": root / "01_QA_CONTACT_SHEETS",
        "primary": root / "02_CURATED" / "PRIMARY_POSE_LINE",
        "detail": root / "02_CURATED" / "POSE_OR_DETAIL",
        "stylized": root / "02_CURATED" / "STYLIZED_ONLY",
        "rejected": root / "99_REJECTED",
        "manifest": root / MANIFEST_NAME,
        "final_manifest": root / FINAL_MANIFEST_NAME,
        "review": root / REVIEW_NAME,
        "cache": root / CACHE_NAME,
        "report": root / REPORT_NAME,
        "archive": workspace.resolve() / "GENERATION_RESULTS",
        "legacy_root": legacy_root,
        "legacy_final_manifest": legacy_root / "FINAL_DOWNLOADED_LINEART_MANIFEST.csv",
    }


def ensure_structure(items: dict[str, Path]) -> None:
    for key in ("root", "incoming", "sheets", "primary", "detail", "stylized", "rejected"):
        items[key].mkdir(parents=True, exist_ok=True)


def clean_html_value(value: str) -> str:
    return html.unescape(value).replace(r'\"', '"').replace("\\/", "/")


def stable_candidate_id(url: str) -> str:
    return f"PL_{hashlib.sha256(url.encode('utf-8')).hexdigest()[:12].upper()}"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: Iterable[dict[str, object]], fields: Iterable[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def load_queries(path: str) -> list[str]:
    if not path:
        return list(DEFAULT_QUERIES)
    query_path = Path(path)
    queries = [line.strip() for line in query_path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    if not queries:
        raise PoseLineError(f"Query file is empty: {query_path}")
    return queries


def request_bytes(url: str, timeout: int, max_bytes: int | None = None) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if max_bytes is None:
            return response.read()
        data = response.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise PoseLineError(f"Download exceeds {max_bytes} bytes")
        return data


def parse_search_results(page: str, query: str) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for alt, original_url, page_url in RESULT_PATTERN.findall(page):
        original_url = clean_html_value(original_url)
        if not original_url:
            continue
        records.append(
            {
                "original_url": original_url,
                "page_url": clean_html_value(page_url),
                "alt": clean_html_value(alt),
                "matched_queries": [query],
            }
        )
    return records


def image_metrics(data: bytes) -> dict[str, object]:
    with Image.open(BytesIO(data)) as opened:
        opened.load()
        rgb = ImageOps.exif_transpose(opened).convert("RGB")
        original_width, original_height = rgb.size
        rgb.thumbnail((512, 512))
        pixels = list(rgb.getdata())
        count = max(1, len(pixels))
        light = sum(1 for r, g, b in pixels if min(r, g, b) >= 220) / count
        dark = sum(1 for r, g, b in pixels if max(r, g, b) <= 75) / count
        chroma = sum(max(r, g, b) - min(r, g, b) for r, g, b in pixels) / count
        edge_mean = ImageStat.Stat(rgb.convert("L").filter(ImageFilter.FIND_EDGES)).mean[0]
        return {
            "width": original_width,
            "height": original_height,
            "format": (opened.format or "PNG").upper(),
            "light_fraction": round(light, 4),
            "dark_fraction": round(dark, 4),
            "mean_chroma": round(chroma, 2),
            "edge_mean": round(edge_mean, 2),
        }


def looks_like_line_art(metrics: dict[str, object]) -> bool:
    return (
        int(metrics["width"]) >= 240
        and int(metrics["height"]) >= 240
        and float(metrics["light_fraction"]) >= 0.28
        and float(metrics["dark_fraction"]) <= 0.58
        and float(metrics["mean_chroma"]) <= 85
        and float(metrics["edge_mean"]) >= 2.0
    )


def collect_search_metadata(items: dict[str, Path], queries: list[str], pages: int, timeout: int) -> list[dict[str, object]]:
    if items["cache"].is_file():
        cache = json.loads(items["cache"].read_text(encoding="utf-8"))
    else:
        cache = {"completed": [], "results": {}, "failed": []}
    completed = set(cache.get("completed", []))
    failed = set(cache.get("failed", []))
    results: dict[str, dict[str, object]] = cache.get("results", {})

    for query in queries:
        for page_number in range(pages):
            checkpoint = f"{query}|{page_number}"
            if checkpoint in completed:
                continue
            params = urllib.parse.urlencode({"text": query, "p": page_number, "isize": "large"})
            url = f"https://yandex.ru/images/search?{params}"
            try:
                page = request_bytes(url, timeout).decode("utf-8", errors="replace")
                records = parse_search_results(page, query)
                if not records:
                    raise PoseLineError("Search page contains no image records")
                for record in records:
                    key = str(record["original_url"])
                    stored = results.setdefault(key, record)
                    stored_queries = list(stored.get("matched_queries", []))
                    stored["matched_queries"] = sorted(set(stored_queries + [query]))
                completed.add(checkpoint)
                failed.discard(checkpoint)
                print(f"SEARCH_PAGE={page_number + 1}/{pages} QUERY={query} UNIQUE={len(results)}", flush=True)
            except Exception as exc:  # network/provider failures must remain resumable
                failed.add(checkpoint)
                print(f"SEARCH_FAILED={checkpoint} REASON={type(exc).__name__}", flush=True)
            cache = {"completed": sorted(completed), "results": results, "failed": sorted(failed)}
            items["cache"].write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    return list(results.values())


def candidate_row(record: dict[str, object]) -> dict[str, object]:
    candidate_id = stable_candidate_id(str(record["original_url"]))
    return {
        "candidate_id": candidate_id,
        "alt": str(record.get("alt", "")),
        "original_url": str(record["original_url"]),
        "page_url": str(record.get("page_url", "")),
        "matched_queries": ";".join(str(value) for value in record.get("matched_queries", [])),
        "rights_status": "UNVERIFIED_LOCAL_REFERENCE_ONLY",
        "publication_status": "DO_NOT_PUBLISH_WITHOUT_LICENSE_REVIEW",
        "qa_status": "PENDING_DOWNLOAD",
        "qa_notes": "",
        "local_path": "",
        "sha256": "",
        "width": "",
        "height": "",
        "format": "",
        "light_fraction": "",
        "dark_fraction": "",
        "mean_chroma": "",
        "edge_mean": "",
    }


def download_candidate(
    items: dict[str, Path], record: dict[str, object], timeout: int, max_bytes: int
) -> dict[str, object]:
    row = candidate_row(record)
    combined = " ".join((str(row["alt"]), str(row["original_url"]), str(row["matched_queries"])))
    if EXCLUDED_METADATA.search(combined):
        row["qa_status"] = "REJECTED_METADATA"
        row["qa_notes"] = "Age-ambiguous, explicit, or excluded metadata term."
        return row

    existing = next(items["incoming"].glob(f"{row['candidate_id']}.*"), None)
    try:
        data = existing.read_bytes() if existing else request_bytes(str(row["original_url"]), timeout, max_bytes)
        if len(data) < 1500:
            raise PoseLineError("Downloaded response is too small")
        metrics = image_metrics(data)
        suffix = SUPPORTED_FORMATS.get(str(metrics["format"]), ".img")
        destination = items["incoming"] / f"{row['candidate_id']}{suffix}"
        if not destination.exists():
            destination.write_bytes(data)
        row.update(metrics)
        row["local_path"] = str(destination)
        row["sha256"] = sha256_bytes(data)
        if looks_like_line_art(metrics):
            row["qa_status"] = "PENDING_VISUAL_QA"
        else:
            row["qa_status"] = "REJECTED_PIXEL_STYLE"
            row["qa_notes"] = "Pixel profile is unlike a light-background line drawing."
    except Exception as exc:
        row["qa_status"] = "DOWNLOAD_FAILED"
        row["qa_notes"] = f"{type(exc).__name__}: {exc}"
    return row


def write_review_template(items: dict[str, Path], rows: list[dict[str, object]]) -> None:
    previous = {row["candidate_id"]: row for row in read_csv(items["review"])}
    review_rows = []
    for row in rows:
        if row["qa_status"] != "PENDING_VISUAL_QA":
            continue
        old = previous.get(str(row["candidate_id"]), {})
        review_rows.append(
            {
                "candidate_id": row["candidate_id"],
                "decision": old.get("decision", ""),
                "notes": old.get("notes", ""),
            }
        )
    write_csv(items["review"], review_rows, ("candidate_id", "decision", "notes"))


def command_collect(args: argparse.Namespace) -> int:
    items = paths(Path(args.workspace))
    ensure_structure(items)
    queries = load_queries(args.queries_file)
    records = collect_search_metadata(items, queries, args.pages, args.timeout)
    records.sort(key=lambda row: str(row["original_url"]))
    if args.max_downloads:
        records = records[: args.max_downloads]

    rows: list[dict[str, object]] = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = [
            pool.submit(download_candidate, items, record, args.timeout, args.max_bytes)
            for record in records
        ]
        for position, future in enumerate(as_completed(futures), 1):
            rows.append(future.result())
            if position % 50 == 0:
                print(f"DOWNLOAD_PROGRESS={position}/{len(futures)}", flush=True)
    rows.sort(key=lambda row: str(row["candidate_id"]))

    seen_hashes: dict[str, str] = {}
    for row in rows:
        digest = str(row.get("sha256", ""))
        if not digest:
            continue
        if digest in seen_hashes:
            row["qa_status"] = "REJECTED_DUPLICATE"
            row["qa_notes"] = f"Duplicate of {seen_hashes[digest]}."
        else:
            seen_hashes[digest] = str(row["candidate_id"])

    write_csv(items["manifest"], rows, CANDIDATE_FIELDS)
    write_review_template(items, rows)
    counts = Counter(str(row["qa_status"]) for row in rows)
    report = {
        "status": "CANDIDATES_READY",
        "provider": "YANDEX_IMAGES_PUBLIC_SEARCH",
        "rights_status": "UNVERIFIED_LOCAL_REFERENCE_ONLY",
        "queries": queries,
        "pages_per_query": args.pages,
        "candidate_count": len(rows),
        "status_counts": dict(sorted(counts.items())),
        "next_action": f"Run build-contact-sheets, visually review every candidate, then fill {items['review']}.",
    }
    items["report"].write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def archive_generated_image(items: dict[str, Path], source: Path) -> Path:
    items["archive"].mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    destination = items["archive"] / f"{stamp}_pose-line-qa-{source.stem.lower()}{source.suffix.lower()}"
    counter = 2
    while destination.exists():
        destination = items["archive"] / f"{stamp}_pose-line-qa-{source.stem.lower()}_{counter:02d}{source.suffix.lower()}"
        counter += 1
    shutil.copy2(source, destination)
    return destination


def build_contact_sheets(items: dict[str, Path], rows: list[dict[str, str]], prefix: str) -> int:
    cols, visible_rows = 6, 4
    cell_w, cell_h, label_h = 260, 340, 34
    font = ImageFont.load_default(size=17)
    sheet_count = 0
    for start in range(0, len(rows), cols * visible_rows):
        batch = rows[start : start + cols * visible_rows]
        canvas = Image.new("RGB", (cols * cell_w, visible_rows * cell_h), "#dedede")
        draw = ImageDraw.Draw(canvas)
        for position, row in enumerate(batch):
            x = (position % cols) * cell_w
            y = (position // cols) * cell_h
            source_path = Path(row["local_path"])
            try:
                with Image.open(source_path) as opened:
                    image = ImageOps.exif_transpose(opened).convert("RGB")
                    image.thumbnail((cell_w - 16, cell_h - label_h - 16), Image.Resampling.LANCZOS)
            except Exception:
                continue
            canvas.paste(image, (x + (cell_w - image.width) // 2, y + label_h + 4))
            draw.rectangle((x, y, x + cell_w - 1, y + cell_h - 1), outline="#777", width=1)
            draw.text((x + 7, y + 7), row["candidate_id"], fill="#111", font=font)
        sheet_count += 1
        destination = items["sheets"] / f"{prefix}_{sheet_count:02d}.jpg"
        canvas.save(destination, quality=92, subsampling=0)
        archive_generated_image(items, destination)
    return sheet_count


def command_build_contact_sheets(args: argparse.Namespace) -> int:
    items = paths(Path(args.workspace))
    ensure_structure(items)
    rows = read_csv(items["manifest"])
    if not rows:
        raise PoseLineError(f"Candidate manifest is missing or empty: {items['manifest']}")
    pending = [row for row in rows if row["qa_status"] == "PENDING_VISUAL_QA" and row["local_path"]]
    rejected = [row for row in rows if row["qa_status"] == "REJECTED_PIXEL_STYLE" and row["local_path"]]
    result = {
        "status": "CONTACT_SHEETS_READY",
        "pending_sheets": build_contact_sheets(items, pending, "PENDING"),
        "rejected_style_sheets": build_contact_sheets(items, rejected, "REJECTED_STYLE"),
        "review_file": str(items["review"]),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def decision_directory(items: dict[str, Path], decision: str) -> Path:
    return {
        "PRIMARY_POSE_LINE": items["primary"],
        "POSE_OR_DETAIL": items["detail"],
        "STYLIZED_ONLY": items["stylized"],
        "REJECTED": items["rejected"],
    }[decision]


def copy_verified(source: Path, destination: Path, expected_hash: str) -> None:
    if destination.exists():
        if sha256_file(destination) != expected_hash:
            raise PoseLineError(f"Existing curated file has a different hash: {destination}")
        return
    shutil.copy2(source, destination)
    if sha256_file(destination) != expected_hash:
        raise PoseLineError(f"Hash mismatch after copying: {destination}")


def command_apply_review(args: argparse.Namespace) -> int:
    items = paths(Path(args.workspace))
    ensure_structure(items)
    candidates = read_csv(items["manifest"])
    review_path = Path(args.review_file).resolve() if args.review_file else items["review"]
    reviews = {row["candidate_id"]: row for row in read_csv(review_path)}
    pending = [row for row in candidates if row["qa_status"] == "PENDING_VISUAL_QA"]
    missing = [row["candidate_id"] for row in pending if reviews.get(row["candidate_id"], {}).get("decision", "").upper() not in REVIEW_DECISIONS]
    if missing:
        preview = ", ".join(missing[:8])
        raise PoseLineError(f"Visual review is incomplete for {len(missing)} candidates: {preview}")

    final_rows: list[dict[str, object]] = []
    counts: Counter[str] = Counter()
    for row in pending:
        review = reviews[row["candidate_id"]]
        decision = review["decision"].upper()
        source = Path(row["local_path"])
        if not source.is_file():
            raise PoseLineError(f"Reviewed source file is missing: {source}")
        digest = row["sha256"]
        if sha256_file(source) != digest:
            raise PoseLineError(f"Downloaded source hash changed: {source}")
        destination = decision_directory(items, decision) / source.name
        copy_verified(source, destination, digest)
        enriched: dict[str, object] = dict(row)
        enriched.update(
            {
                "review_decision": decision,
                "review_notes": review.get("notes", ""),
                "curated_path": str(destination),
                "curated_sha256": digest,
                "allowed_role": "POSE_SOFT" if decision in USABLE_DECISIONS else "NONE",
                "forbidden_roles": "BODY;BODY_BUILD_TARGET;FACE;STYLE;CLOTHES;PHYSIOLOGY",
                "influence_policy": "JOINTS;GESTURE;BALANCE;FORESHORTENING;CONTACTS_ONLY",
            }
        )
        final_rows.append(enriched)
        counts[decision] += 1

    final_fields = list(CANDIDATE_FIELDS) + [
        "review_decision", "review_notes", "curated_path", "curated_sha256",
        "allowed_role", "forbidden_roles", "influence_policy",
    ]
    write_csv(items["final_manifest"], final_rows, final_fields)
    result = {
        "status": "READY" if counts["PRIMARY_POSE_LINE"] >= args.minimum_primary else "BELOW_TARGET",
        "counts": dict(sorted(counts.items())),
        "minimum_primary": args.minimum_primary,
        "manifest": str(items["final_manifest"]),
        "rights_status": "UNVERIFIED_LOCAL_REFERENCE_ONLY",
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "READY" else 2


def status_payload(items: dict[str, Path]) -> dict[str, object]:
    final_rows = read_csv(items["final_manifest"])
    if final_rows:
        counts = Counter(row.get("review_decision", "UNKNOWN") for row in final_rows)
        return {
            "status": "READY" if counts["PRIMARY_POSE_LINE"] >= 200 else "BELOW_TARGET",
            "library": str(items["root"]),
            "counts": dict(sorted(counts.items())),
            "primary_count": counts["PRIMARY_POSE_LINE"],
            "usage": "SOFT_POSE_ONLY_INDEPENDENT_OF_BODY_REFERENCE_LIBRARY",
            "rights_status": "UNVERIFIED_LOCAL_REFERENCE_ONLY",
        }
    legacy_rows = read_csv(items["legacy_final_manifest"])
    if legacy_rows:
        counts = Counter(row.get("final_tier", "UNKNOWN") for row in legacy_rows)
        return {
            "status": "READY" if counts["PRIMARY_LINE_ART"] >= 200 else "BELOW_TARGET",
            "library": str(items["legacy_root"]),
            "source_layout": "EXISTING_ANATOMY_CONTOUR_LIBRARY",
            "counts": dict(sorted(counts.items())),
            "primary_count": counts["PRIMARY_LINE_ART"],
            "usage": "SOFT_POSE_ONLY_INDEPENDENT_OF_BODY_REFERENCE_LIBRARY",
            "rights_status": "UNVERIFIED_LOCAL_REFERENCE_ONLY",
        }
    candidates = read_csv(items["manifest"])
    if candidates:
        counts = Counter(row.get("qa_status", "UNKNOWN") for row in candidates)
        return {
            "status": "REVIEW_REQUIRED",
            "library": str(items["root"]),
            "candidate_counts": dict(sorted(counts.items())),
            "review_file": str(items["review"]),
            "next_action": "Build contact sheets and complete visual review.",
        }
    return {
        "status": "NOT_BUILT",
        "library": str(items["root"]),
        "offer": "Ask the user whether to build a local 200+ female-focused pose-line library.",
        "collect_command": ".\\scripts\\bootstrap.ps1 -CollectPoseLineLibrary",
    }


def command_status(args: argparse.Namespace) -> int:
    payload = status_payload(paths(Path(args.workspace)))
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"STATUS={payload['status']}")
        print(f"LIBRARY={payload['library']}")
        if payload.get("offer"):
            print(f"OFFER={payload['offer']}")
            print(f"COLLECT_COMMAND={payload['collect_command']}")
    return 0


def command_validate(args: argparse.Namespace) -> int:
    items = paths(Path(args.workspace))
    rows = read_csv(items["final_manifest"])
    legacy = False
    if not rows:
        rows = read_csv(items["legacy_final_manifest"])
        legacy = bool(rows)
    if not rows:
        raise PoseLineError(f"Curated manifest is missing or empty: {items['final_manifest']}")
    errors: list[str] = []
    for row in rows:
        path = Path(row["curated_path"])
        if not path.is_file():
            errors.append(f"missing:{row['candidate_id']}")
        elif sha256_file(path) != row["curated_sha256"]:
            errors.append(f"hash:{row['candidate_id']}")
        if not legacy and row["review_decision"] in USABLE_DECISIONS and row["allowed_role"] != "POSE_SOFT":
            errors.append(f"role:{row['candidate_id']}")
    counts = Counter(row["final_tier"] if legacy else row["review_decision"] for row in rows)
    primary_key = "PRIMARY_LINE_ART" if legacy else "PRIMARY_POSE_LINE"
    payload = {
        "status": "VALID" if not errors and counts[primary_key] >= args.minimum_primary else "INVALID",
        "checked": len(rows),
        "primary_count": counts[primary_key],
        "source_layout": "EXISTING_ANATOMY_CONTOUR_LIBRARY" if legacy else "POSE_LINE_REFERENCE_LIBRARY",
        "errors": errors,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["status"] == "VALID" else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a local, visually reviewed pose-line reference library.")
    parser.add_argument("--workspace", default=str(WORKSPACE), help="StoryArt workspace root.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    collect = subparsers.add_parser("collect", help="Download line-art candidates into the ignored local library.")
    collect.add_argument("--pages", type=int, default=4, help="Search-result pages per query.")
    collect.add_argument("--workers", type=int, default=8, help="Parallel image downloads.")
    collect.add_argument("--timeout", type=int, default=35, help="Network timeout in seconds.")
    collect.add_argument("--max-bytes", type=int, default=16 * 1024 * 1024, help="Maximum bytes per image.")
    collect.add_argument("--max-downloads", type=int, default=600, help="Maximum unique candidate URLs per run.")
    collect.add_argument("--queries-file", default="", help="Optional UTF-8 file with one search query per line.")
    collect.set_defaults(func=command_collect)

    sheets = subparsers.add_parser("build-contact-sheets", help="Build local QA sheets and archive their generated copies.")
    sheets.set_defaults(func=command_build_contact_sheets)

    apply_review = subparsers.add_parser("apply-review", help="Apply a complete visual-review CSV non-destructively.")
    apply_review.add_argument("--review-file", default="", help=f"Defaults to local {REVIEW_NAME}.")
    apply_review.add_argument("--minimum-primary", type=int, default=200)
    apply_review.set_defaults(func=command_apply_review)

    status = subparsers.add_parser("status", help="Report whether the local library should be offered, reviewed, or used.")
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=command_status)

    validate = subparsers.add_parser("validate", help="Verify curated files, hashes, role policy, and minimum size.")
    validate.add_argument("--minimum-primary", type=int, default=200)
    validate.set_defaults(func=command_validate)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return int(args.func(args))
    except PoseLineError as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
