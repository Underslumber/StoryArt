"""Import an explicitly exported image catalogue; never copies website code.

The input is the six public gallery views (title/category/image URL/alt text).
Images remain unchanged. Publisher metadata does not confirm subject sex or
anatomical correctness: those fields fail closed until separately reviewed.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import re
import sys
import urllib.request

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
POOL = "WAVESPEED_POSES"
COLLECTION = "WAVESPEED_POSES_SOURCE_COLLECTION"
HOME = "https://wavespeed.ai/free-tools/ai-pose-gallery"
STYLES = {"3D mannequin": "3d", "Line art": "line"}
ANGLES = {"Front": "front", "3/4": "three_quarter", "Side": "side"}
REVIEW_FIELDS = {'subject_label', 'subject_label_status', 'subject_label_sha256',
                'subject_label_evidence', 'subject_review_id', 'reviewer', 'viewed',
                'visual_qa_status', 'anatomy_qa_status', 'visual_review_evidence',
                'visual_review_sha256', 'review_limitations', 'review_complete',
                'curation_status', 'curation_reason', 'participants', 'author_review_requested'}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inventory(data):
    """Require all six independently observed views and matching pose order."""
    if data.get('source') != HOME:
        raise ValueError('Catalogue source must be the public pose gallery')
    variants = data["variants"]
    combinations = {(v["style"], v["angle"]) for v in variants}
    if len(variants) != 6 or combinations != {(s, a) for s in STYLES for a in ANGLES}:
        raise ValueError("Exactly six style/angle views required")
    baseline = [(r["category"], r["title"]) for r in variants[0]["rows"]]
    if len(baseline) != 491 or len(set(baseline)) != len(baseline):
        raise ValueError("Expected 491 unique category/title pairs")
    rows = []
    for v in variants:
        if [(r["category"], r["title"]) for r in v["rows"]] != baseline:
            raise ValueError("Pose order differs between views")
        for i, source in enumerate(v["rows"], 1):
            match = re.fullmatch(r"https://img\.wavespeed\.ai/[^?]+/media/images/([a-f0-9-]{36}\.webp)", source["src"])
            if not match:
                raise ValueError("Unexpected public image URL")
            # The public viewer's Make-this-photo link publishes this original
            # static host/path; fit-in URLs are display derivatives only.
            url = "https://static.wavespeed.ai/media/images/" + match[1]
            representation, angle = STYLES[v["style"]], ANGLES[v["angle"]]
            if not source["alt"].endswith(f", {v['angle']} view, {v['style']}"):
                raise ValueError("Image alt disagrees with selected view")
            ident = f"WS_{i:04d}_{representation}_{angle}"
            rows.append({"id": ident, "group_id": f"WS_{i:04d}",
                         "title": source["title"], "category": source["category"],
                         "angle": angle, "representation": representation,
                         "relative_path": f"images/{ident}.webp", "source_url": url,
                         "source_alt": source["alt"], "subject_label": "UNKNOWN",
                         "subject_label_status": "QUARANTINED",
                         "subject_label_evidence": "Publisher title/category/alt do not declare a unique subject sex.",
                         "visual_qa_status": "NOT_CHECKED", "anatomy_qa_status": "NOT_CHECKED"})
    if len({r['source_url'] for r in rows}) != len(rows):
        raise ValueError("Duplicate image URLs across declared views")
    return rows


def download_original(url, timeout=15):
    """Use ordinary byte ranges: some CDN responses stall on large bodies."""
    chunk_size, limit = 8192, 8 * 1024 * 1024
    payload, total, etag = bytearray(), None, None
    while total is None or len(payload) < total:
        start = len(payload)
        headers = {'User-Agent':'StoryArt-reference-packs/1',
                   'Range':f'bytes={start}-{start+chunk_size-1}', 'Accept-Encoding':'identity'}
        if etag: headers['If-Range'] = etag
        with urllib.request.urlopen(urllib.request.Request(url,headers=headers),timeout=timeout) as response:
            if response.status == 200 and start == 0:
                body = response.read(limit+1)
                if len(body)>limit: raise ValueError('Image exceeds the expected source size limit')
                return body
            match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)',response.headers.get('Content-Range',''))
            if response.status != 206 or not match:
                raise ValueError('Original byte-range response missing or changed')
            first,last,size = map(int,match.groups())
            if first != start or last != min(start+chunk_size,size)-1 or not 0<size<=limit:
                raise ValueError('Unexpected original byte-range bounds')
            if total is not None and size != total: raise ValueError('Original size changed during download')
            current_etag = response.headers.get('ETag')
            if etag is not None and current_etag != etag: raise ValueError('Original ETag changed during download')
            etag, total = current_etag, size
            body = response.read(chunk_size+1)
            if len(body) != last-first+1: raise ValueError('Incomplete original byte range')
            payload.extend(body)
    return bytes(payload)


def acquire(row, root, timeout=15):
    path = root / row["relative_path"]
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        temporary = path.with_suffix(".download")
        try:
            temporary.write_bytes(download_original(row['source_url'],timeout))
            with Image.open(temporary) as im:
                if im.format != "WEBP" or im.size != (900, 1200):
                    raise ValueError(f"Unexpected image format/dimensions: {im.format} {im.size}")
                im.verify()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    with Image.open(path) as im:
        if im.format != "WEBP" or im.size != (900, 1200):
            raise ValueError("Existing image has wrong format/dimensions")
        im.verify()
    return {**row, "sha256": digest(path), "size": path.stat().st_size,
            "width": 900, "height": 1200, "file_check": "DECODE_AND_DIMENSIONS_PASS"}


def apply_reviews(manifest, reviews, root, review_hash):
    """Apply explicit inspection evidence without guessing sex or approving art."""
    by_id = {row['id']:row for row in manifest['records']}
    if len(by_id) != len(manifest['records']):
        raise ValueError('Duplicate manifest IDs')
    seen = set()
    updates = []
    for review in reviews['reviews']:
        if set(review) - (REVIEW_FIELDS | {'id', 'sha256'}):
            raise ValueError('Review may not change source identity, paths or provenance')
        ident = review['id']
        if ident in seen or ident not in by_id:
            raise ValueError('Duplicate or unknown review ID')
        seen.add(ident)
        row = by_id[ident]
        label = review['subject_label']
        if label not in {'MALE','FEMALE','NEUTRAL','MIXED','UNKNOWN'}:
            raise ValueError('Invalid subject label')
        if review['sha256'] != row['sha256'] or digest(root/row['relative_path']) != row['sha256']:
            raise ValueError('Review/source hash differs')
        if not review.get('subject_label_evidence') or not review.get('reviewer'):
            raise ValueError('Subject evidence and reviewer required')
        visual, anatomy = review.get('visual_qa_status','NOT_CHECKED'), review.get('anatomy_qa_status','NOT_CHECKED')
        if visual not in {'PASS','FAIL','NOT_CHECKED'} or anatomy not in {'PASS','FAIL','NOT_CHECKED'}:
            raise ValueError('Invalid QA status')
        if 'PASS' in (visual,anatomy) and (review.get('viewed') != 'FULL_RESOLUTION'
                or not review.get('visual_review_evidence') or not review.get('review_limitations')):
            raise ValueError('Visual/anatomy PASS requires full-resolution evidence and limitations')
        disposition = review.get('curation_status')
        if disposition is not None:
            if disposition not in {'READY','EXCLUDED'} or review.get('review_complete') is not True or not review.get('curation_reason'):
                raise ValueError('Curation requires a completed review and an explicit READY/EXCLUDED reason')
            from tools.wavespeed_pose_pool import participants_confirmed
            if disposition == 'READY' and (label not in {'MALE','FEMALE','NEUTRAL','MIXED'}
                    or visual != 'PASS' or anatomy != 'PASS'
                    or (label=='MIXED' and not participants_confirmed({**row,**review}))):
                raise ValueError('READY requires confirmed matching-sex subject and full pose QA PASS')
        from tools.wavespeed_pose_pool import participants_confirmed
        confirmed=label in {'MALE','FEMALE','NEUTRAL'} or (label=='MIXED' and participants_confirmed({**row,**review}))
        updates.append((row, {**{k:v for k,v in review.items() if k in REVIEW_FIELDS},'subject_label_status':'CONFIRMED' if confirmed else 'QUARANTINED',
                              'subject_label_sha256':row['sha256'],'subject_review_id':review_hash+':'+ident,
                              'visual_qa_status':visual,'anatomy_qa_status':anatomy,
                              'visual_review_sha256':row['sha256']}))
    # Validate every review before changing any row.
    for row, update in updates: row.update(update)
    return len(updates)


def preserve_reviews(rows, previous):
    by_id = {r['id']:r for r in previous.get('records',[])}
    for row in rows:
        old = by_id.get(row['id'],{})
        if old.get('sha256') == row['sha256'] and old.get('source_url') == row['source_url']:
            row.update({k:old[k] for k in REVIEW_FIELDS if k in old})


def finalize_curation(manifest, root):
    """Completeness is about all original hashes, not only the ready subset."""
    rows = manifest['records']
    expected = {f'WS_{n:04d}_{style}_{angle}' for n in range(1,492)
                for style in ('3d','line') for angle in ('front','side','three_quarter')}
    if len(rows) != 2946 or {r['id'] for r in rows} != expected:
        raise ValueError('Complete curation requires every one of the 2946 originals')
    for row in rows:
        if (row.get('review_complete') is not True or row.get('curation_status') not in {'READY','EXCLUDED'}
                or not row.get('curation_reason') or row.get('viewed') != 'FULL_RESOLUTION'
                or row.get('visual_qa_status') not in {'PASS','FAIL'}
                or row.get('anatomy_qa_status') not in {'PASS','FAIL'}
                or row.get('visual_review_sha256') != row.get('sha256')
                or row.get('subject_label_sha256') != row.get('sha256')
                or not row.get('subject_review_id') or not row.get('visual_review_evidence')
                or not row.get('review_limitations') or digest(root/row['relative_path']) != row['sha256']):
            raise ValueError('Unfinished or stale individual curation: '+row['id'])
        from tools.wavespeed_pose_pool import subject_confirmed
        if row['curation_status']=='READY' and (not subject_confirmed(row)
                or row['visual_qa_status']!='PASS' or row['anatomy_qa_status']!='PASS'):
            raise ValueError('Unsuitable source marked READY: '+row['id'])
    ready = [r for r in rows if r['curation_status']=='READY']
    if not ready: raise ValueError('No usable pose references in completed curation')
    manifest['curation']={'status':'COMPLETE','reviewed_originals':len(rows),
                          'ready':len(ready),'excluded':len(rows)-len(ready),
                          'ready_groups':len({r['group_id'] for r in ready}),
                          'scope':'Connected-body pose/support/camera; exact per-image observations and limitations apply.'}
    return manifest['curation']


def save_json(path, data):
    temporary = path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalogue", type=Path)
    parser.add_argument("--review-file", type=Path)
    parser.add_argument("--apply-reviews-only", action='store_true')
    parser.add_argument("--require-complete-curation", action='store_true')
    parser.add_argument("--workspace", type=Path, default=ROOT)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--timeout", type=float, default=15)
    args = parser.parse_args()
    if args.timeout <= 0: parser.error('--timeout must be positive')
    if args.apply_reviews_only:
        if not args.review_file: parser.error('--apply-reviews-only requires --review-file')
        root = args.workspace.resolve()/COLLECTION
        path = root/'POSES.json'
        manifest = json.loads(path.read_text('utf-8'))
        count = apply_reviews(manifest,json.loads(args.review_file.read_text('utf-8-sig')),root,digest(args.review_file))
        if args.require_complete_curation: finalize_curation(manifest,root)
        save_json(path,manifest)
        print(json.dumps({'reviewed':count})); return 0
    if args.require_complete_curation: parser.error('--require-complete-curation requires --apply-reviews-only')
    if not args.catalogue: parser.error('--catalogue required for import')
    if args.review_file: parser.error('--review-file requires --apply-reviews-only')
    data = json.loads(args.catalogue.read_text(encoding="utf-8-sig"))
    rows = inventory(data)
    root = args.workspace.resolve() / COLLECTION
    completed, errors = [], []
    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, 8))) as executor:
        futures = {executor.submit(acquire, row, root, args.timeout): row for row in rows}
        for future in as_completed(futures):
            try:
                completed.append(future.result())
            except Exception as error:
                errors.append({"id": futures[future]["id"], "error": str(error)})
            if (len(completed) + len(errors)) % 100 == 0:
                print(f"checked={len(completed)} failed={len(errors)} total={len(rows)}", flush=True)
                save_json(root/'DOWNLOAD_REPORT.json',{'expected':len(rows),'downloaded_and_verified':len(completed),'errors':errors,'status':'IN_PROGRESS'})
    report = {"expected": len(rows), "downloaded_and_verified": len(completed), "errors": errors}
    root.mkdir(parents=True, exist_ok=True)
    (root / "DOWNLOAD_REPORT.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if errors:
        print(json.dumps(report, ensure_ascii=False)); return 1
    manifest_path = root/'POSES.json'
    if manifest_path.is_file():
        preserve_reviews(completed,json.loads(manifest_path.read_text('utf-8')))
    manifest = {"schema_version": 1, "pool": POOL, "source": HOME,
                "pose_count": 491, "image_count": 2946,
                "rights": {"reference_use": "PERSONAL_AND_COMMERCIAL",
                           "standalone_pack_resale": "FORBIDDEN",
                           "notice": "WaveSpeed AI originals. Preserve source terms; no unrestricted relicensing claimed.",
                           "terms_url": HOME},
                "records": sorted(completed, key=lambda r: r["id"])}
    save_json(manifest_path,manifest)
    print(json.dumps({**report, "bytes": sum(r['size'] for r in completed)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"): sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
