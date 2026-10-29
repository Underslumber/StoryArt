"""Build and query a bounded, provenance-preserving external reference catalog.

This catalog is a discovery aid. Its selections are candidates for later review
and existing StoryArt approval workflows; they are never generation-ready.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlparse

SCHEMA_VERSION = 1
CATEGORIES = {
    "CLOTHING", "ACCESSORIES", "FABRIC_AND_FIT", "ANATOMY", "POSE_AND_GESTURE",
    "FACE_AND_EXPRESSION", "HAIR", "LIGHTING_AND_COLOR", "DRAWING_TECHNIQUE", "OTHER",
}
UNKNOWN = "UNKNOWN"
POST_URL_RE = re.compile(r"^https://(?:www\.)?x\.com/([A-Za-z0-9_]+)/status/(\d+)/?(?:\?.*)?$")
MEDIA_URL_RE = re.compile(r"^https://pbs\.twimg\.com/media/[A-Za-z0-9_-]+(?:\.[A-Za-z0-9]+)?(?:\?.*)?$")


def _load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return default


def _post_id(url: str) -> str:
    parsed = urlparse(url)
    parts = [p for p in parsed.path.split("/") if p]
    candidate = parts[-1] if parts else ""
    return candidate if re.fullmatch(r"[A-Za-z0-9_-]+", candidate) else hashlib.sha256(url.encode()).hexdigest()[:16]


def _valid_post_source(post: dict[str, Any]) -> bool:
    url = str(post.get("url", ""))
    match = POST_URL_RE.fullmatch(url)
    if match and match.group(1).casefold() == "oldmanlux":
        return True
    if match and post.get("discovered_via") == "https://x.com/oldmanlux" and post.get("relationship") == "REPOST":
        return True
    return bool(post.get("source_kind") == "USER_ATTACHMENT" and post.get("attachment_id") and url == f"local://attachment/{post.get('attachment_id')}")


def _valid_media_url(url: Any) -> bool:
    return bool(isinstance(url, str) and MEDIA_URL_RE.fullmatch(url))


def _valid_asset_media_url(url: Any, post: dict[str, Any]) -> bool:
    attachment_url = f"local://attachment/{post.get('attachment_id')}"
    return _valid_media_url(url) or bool(post.get("source_kind") == "USER_ATTACHMENT" and post.get("attachment_id") and url == attachment_url)


def _valid_asset_id(aid: str, post_id: str, media_index: Any, post: dict[str, Any]) -> bool:
    if post.get("source_kind") == "USER_ATTACHMENT" and post.get("attachment_id") == "OLX_USER_TOPS_01":
        return aid == "OLX_USER_TOPS_01"
    return isinstance(media_index, int) and media_index > 0 and aid == f"OLX_{post_id}_{media_index:02d}"


def _safe_file(root: Path, rel: str) -> Path | None:
    """Resolve a catalog-relative path, rejecting absolute paths and escapes."""
    if not isinstance(rel, str) or not rel.strip():
        return None
    normalized = rel.replace("\\", "/")
    p = PurePosixPath(normalized)
    if p.is_absolute() or any(part in ("..", "") for part in p.parts):
        return None
    root_resolved = root.resolve()
    try:
        resolved = (root_resolved / Path(*p.parts)).resolve(strict=False)
        resolved.relative_to(root_resolved)
    except (ValueError, OSError):
        return None
    return resolved


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _clean_tags(value: Any) -> list[str]:
    tags = value if isinstance(value, list) else []
    return sorted({str(tag).strip() for tag in tags if re.fullmatch(r"[A-Z][A-Z0-9_]*:[A-Z0-9_]+", str(tag).strip())})


def build_catalog(root: Path) -> dict[str, Any]:
    posts_doc = _load_json(root / "source_posts.json", {})
    downloads_doc = _load_json(root / "downloads.json", {})
    reviews_doc = _load_json(root / "reviews.json", {})
    posts_raw = posts_doc.get("posts", []) if isinstance(posts_doc, dict) else []
    downloads = downloads_doc.get("assets", []) if isinstance(downloads_doc, dict) else []
    reviews_raw = reviews_doc.get("reviews", []) if isinstance(reviews_doc, dict) else []
    posts: dict[str, dict[str, Any]] = {}
    for post in posts_raw if isinstance(posts_raw, list) else []:
        if not isinstance(post, dict):
            continue
        url = str(post.get("url", "")).strip()
        if not _valid_post_source(post):
            continue
        posts[url] = {**post, "post_id": _post_id(url)}
    reviews: dict[str, dict[str, Any]] = {}
    duplicate_reviews: set[str] = set()
    for review in reviews_raw if isinstance(reviews_raw, list) else []:
        if not isinstance(review, dict) or not review.get("asset_id"):
            continue
        aid = str(review["asset_id"])
        if aid in reviews:
            duplicate_reviews.add(aid)
        reviews[aid] = review
    rows: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for asset in downloads if isinstance(downloads, list) else []:
        if not isinstance(asset, dict):
            continue
        aid = str(asset.get("asset_id", ""))
        if not aid or aid in seen_ids:
            # Preserve duplicates as diagnosable rows with an invalid status.
            dup = aid in seen_ids
        else:
            dup = False
            seen_ids.add(aid)
        post_url = str(asset.get("post_url", ""))
        post = posts.get(post_url, {})
        review = reviews.get(aid, {})
        rel_path = str(asset.get("local_path", ""))
        file_path = _safe_file(root, rel_path)
        exists = bool(file_path and file_path.is_file())
        actual_hash = _sha256(file_path) if exists and file_path else ""
        declared_hash = str(asset.get("sha256", "")).lower()
        hash_ok = bool(actual_hash and declared_hash == actual_hash)
        review_current = bool(review and hash_ok and str(review.get("sha256", "")).lower() == actual_hash and aid not in duplicate_reviews)
        classification_current = review_current and str(review.get("category", "")).upper() in CATEGORIES
        category = str(review.get("category", "OTHER")).upper() if classification_current else "OTHER"
        classification_basis = str(review.get("classification_basis", "VISUAL")).upper() if classification_current else "NONE"
        visual_review = str(review.get("visual_review", "NOT_CHECKED")).upper() if review_current else "NOT_CHECKED"
        subject_label = str(review.get("subject_label", UNKNOWN)).upper() if review_current else UNKNOWN
        subject_evidence = review.get("subject_label_evidence", UNKNOWN) if review_current else UNKNOWN
        visually_reviewed = review_current and visual_review == "PASS" and subject_label not in {"", UNKNOWN, "MIXED", "NEUTRAL"} and subject_evidence not in {None, "", UNKNOWN}
        images = post.get("images", []) if isinstance(post.get("images"), list) else []
        idx = asset.get("media_index")
        image_meta = images[idx - 1] if isinstance(idx, int) and 1 <= idx <= len(images) and isinstance(images[idx - 1], dict) else {}
        status = "REVIEWED" if visually_reviewed else ("CLASSIFIED" if classification_current else "PENDING_REVIEW")
        if dup:
            status = "INVALID_DUPLICATE_ID"
        elif not _valid_asset_id(aid, post.get("post_id", _post_id(post_url) if post_url else ""), idx, post):
            status = "INVALID_ASSET_ID"
        elif not post or not _valid_post_source(post):
            status = "INVALID_POST_URL"
        elif not _valid_asset_media_url(asset.get("source_url", image_meta.get("src", "")), post) or not _valid_asset_media_url(asset.get("download_url", asset.get("source_url", image_meta.get("src", ""))), post):
            status = "INVALID_MEDIA_URL"
        elif str(asset.get("download_status", "")).upper() not in {"DOWNLOADED", "OK", "SUCCESS"}:
            status = "DOWNLOAD_PENDING"
        elif not file_path:
            status = "INVALID_PATH"
        elif not exists:
            status = "MISSING_FILE"
        elif not hash_ok:
            status = "HASH_MISMATCH"
        tags = _clean_tags(review.get("tags")) if classification_current else []
        row = {
            "asset_id": aid, "post_id": post.get("post_id", _post_id(post_url) if post_url else ""),
            "post_url": post_url, "source_author": post.get("source_author", (POST_URL_RE.fullmatch(post_url).group(1) if POST_URL_RE.fullmatch(post_url) else "USER_ATTACHMENT")),
            "relationship": post.get("relationship", "ORIGINAL"), "discovered_via": post.get("discovered_via", "https://x.com/oldmanlux" if post.get("source_author", "oldmanlux").casefold() == "oldmanlux" else ""),
            "source_url": asset.get("source_url", image_meta.get("src", "")),
            "download_url": asset.get("download_url", ""), "media_source_post_url": image_meta.get("source_post_url", ""),
            "media_context_post_url": asset.get("media_context_post_url", post_url),
            "local_path": rel_path, "sha256": actual_hash or declared_hash,
            "duplicate_of": "", "width": asset.get("width"), "height": asset.get("height"), "bytes": asset.get("bytes"), "format": asset.get("format", ""),
            "declared_sha256": declared_hash, "hash_verified": hash_ok,
            "media_index": idx, "alt": image_meta.get("alt", ""),
            "text": post.get("text", ""), "rendered_text": post.get("rendered_text", ""),
            "time": post.get("time", ""), "observed_at": post.get("observed_at", ""),
            "links": post.get("links", []), "has_video": bool(post.get("has_video", False)),
            "category": category, "classification_basis": classification_basis, "classification_origin": "CAPTION_DERIVED" if classification_basis == "CAPTION" else ("VISUAL_REVIEW" if classification_basis == "VISUAL" else "UNCLASSIFIED"),
            "tags": tags, "title_ru": review.get("title_ru", "") if classification_current else "",
            "summary_ru": review.get("summary_ru", "") if classification_current else "",
            "subject_label": subject_label if visually_reviewed else UNKNOWN,
            "subject_label_evidence": subject_evidence if visually_reviewed else UNKNOWN,
            "visual_review": visual_review,
            "example_content": review.get("example_content", "UNKNOWN") if review_current else "UNKNOWN",
            "requires_positive_crop": bool(review.get("requires_positive_crop", False)) if review_current else True,
            "raw_generation_attachment": False,
            "curation_status": review.get("curation_status", "RESEARCH_ONLY") if review_current else "RESEARCH_ONLY",
            "curation_reason": review.get("curation_reason", "") if review_current else "Unreviewed current source",
            "source_clean_qa": review.get("source_clean_qa", "NOT_CHECKED") if review_current else "NOT_CHECKED",
            "source_clean_qa_sha256": review.get("source_clean_qa_sha256", "") if review_current else "",
            "allowed_uses": review.get("allowed_uses", []) if classification_current else [],
            "forbidden_uses": review.get("forbidden_uses", []) if classification_current else [],
            "advice": review.get("advice", []) if classification_current else [],
            "rights_status": review.get("rights_status", "UNKNOWN") if review_current else "UNKNOWN",
            "status": status,
        }
        rows.append(row)
    # Keep text-only posts searchable and retain unreviewed advice/source text.
    asset_post_urls = {str(a.get("post_url", "")) for a in downloads if isinstance(a, dict)}
    for url, post in posts.items():
        if url in asset_post_urls:
            continue
        match = POST_URL_RE.fullmatch(url)
        rows.append({"asset_id": "", "post_id": post["post_id"], "post_url": url,
                     "source_author": post.get("source_author", match.group(1) if match else "USER_ATTACHMENT"),
                     "relationship": post.get("relationship", "ORIGINAL"), "discovered_via": post.get("discovered_via", ""),
                     "source_url": "", "local_path": "", "sha256": "", "declared_sha256": "",
                     "hash_verified": False, "media_index": None, "alt": "", "text": post.get("text", ""),
                     "rendered_text": post.get("rendered_text", ""), "time": post.get("time", ""),
                     "observed_at": post.get("observed_at", ""), "links": post.get("links", []),
                     "has_video": bool(post.get("has_video", False)), "category": "OTHER", "tags": [],
                     "title_ru": "", "summary_ru": "", "subject_label": UNKNOWN,
                     "subject_label_evidence": UNKNOWN, "visual_review": "NOT_REVIEWED",
                     "allowed_uses": [], "forbidden_uses": [], "advice": [], "rights_status": "UNKNOWN",
                     "status": "POST_ONLY_PENDING_REVIEW"})
    coverage = posts_doc.get("coverage", {}) if isinstance(posts_doc, dict) else {}
    by_hash: dict[str, str] = {}
    for row in rows:
        if row.get("asset_id") and row.get("sha256"):
            row["duplicate_of"] = by_hash.get(row["sha256"], "")
            by_hash.setdefault(row["sha256"], row["asset_id"])
    return {"schema_version": SCHEMA_VERSION, "source_account": posts_doc.get("source_account", "oldmanlux") if isinstance(posts_doc, dict) else "oldmanlux",
            "coverage": coverage, "coverage_warning": "Coverage reflects acquired inputs only; it does not prove complete account history review.",
            "items": sorted(rows, key=lambda r: (r["post_id"], r["asset_id"]))}


def _json_safe_script(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False).replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def _gallery(catalog: dict[str, Any]) -> str:
    payload = _json_safe_script(catalog["items"])
    return f'''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Oldmanlux — локальный каталог</title>
<style>body{{font:16px system-ui;margin:2rem;max-width:1200px}}.warning{{padding:1rem;background:#fff2cc}}.filters{{display:flex;gap:.6rem;flex-wrap:wrap}}input,select{{padding:.5rem}}article{{border-top:1px solid #bbb;padding:1rem 0;display:grid;grid-template-columns:minmax(150px,320px) 1fr;gap:1rem}}img{{max-width:100%;max-height:320px;object-fit:contain}}small{{color:#555}}pre{{white-space:pre-wrap;overflow-wrap:anywhere}}@media(max-width:650px){{article{{grid-template-columns:1fr}}}}</style>
<h1>Локальный каталог oldmanlux</h1><p class="warning">{html.escape(catalog['coverage_warning'])} Покрытие: {html.escape(json.dumps(catalog.get('coverage', {}), ensure_ascii=False))}</p>
<div class="filters"><input id="q" placeholder="Поиск по тексту и тегам"><select id="cat"><option value="">Все категории</option>{''.join(f'<option>{c}</option>' for c in sorted(CATEGORIES))}</select><select id="status"><option value="">Все статусы</option><option>REVIEWED</option><option>PENDING_REVIEW</option><option>DOWNLOAD_PENDING</option><option>POST_ONLY_PENDING_REVIEW</option><option>HASH_MISMATCH</option></select></div><p id="count"></p><main id="items"></main>
<script type="application/json" id="data">{payload}</script><script>
const data=JSON.parse(document.getElementById('data').textContent), q=document.getElementById('q'),cat=document.getElementById('cat'),status=document.getElementById('status'),root=document.getElementById('items');
function esc(s){{return String(s??'').replace(/[&<>"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]))}}
function localPath(p){{if(typeof p!=='string'||!p.length)return false;let d;try{{d=decodeURIComponent(p)}}catch{{return false}}return !d.startsWith('/')&&!d.includes('\\\\')&&!d.split('/').some(s=>!s||s==='.'||s==='..')&&!/^[a-z][a-z0-9+.-]*:/i.test(d)}}
function postUrl(u){{if(typeof u!=='string')return false;try{{const z=new URL(u);const p=z.pathname.split('/').filter(Boolean);return z.protocol==='https:'&&z.hostname==='x.com'&&p.length===3&&p[1]==='status'&&/^\d+$/.test(p[2])}}catch{{return u.startsWith('local://attachment/')}}}}
function mediaUrl(u){{return typeof u==='string'&&/^https:\/\/pbs\.twimg\.com\/media\/[A-Za-z0-9_-]+(?:\.[A-Za-z0-9]+)?(?:\?.*)?$/.test(u)}}
function render(){{const term=q.value.toLocaleLowerCase();const list=data.filter(x=>(!cat.value||x.category===cat.value)&&(!status.value||x.status===status.value)&&(!term||JSON.stringify([x.text,x.rendered_text,x.title_ru,x.summary_ru,x.tags,x.advice]).toLocaleLowerCase().includes(term)));document.getElementById('count').textContent=`Показано: ${{list.length}} / ${{data.length}}`;root.innerHTML=list.map(x=>{{const lp=localPath(x.local_path),pu=postUrl(x.post_url),mu=mediaUrl(x.source_url);return `<article>${{lp?`<a href="${{esc(x.local_path)}}"><img loading="lazy" src="${{esc(x.local_path)}}" alt="${{esc(x.alt)}}"></a>`:''}}<section><h2>${{esc(x.title_ru||x.asset_id||('Пост '+x.post_id))}}</h2><small>${{esc(x.status)}} · ${{esc(x.category)}} · rights: ${{esc(x.rights_status)}} · sha256: ${{esc(x.sha256)}}</small><p>${{esc(x.summary_ru||'')}}</p><pre>${{esc(x.rendered_text||x.text||'')}}</pre><p>Теги: ${{esc((x.tags||[]).join(', '))}}</p>${{pu?`<a href="${{esc(x.post_url)}}" target="_blank" rel="noopener">Источник поста</a>`:''}}${{mu?` · <a href="${{esc(x.source_url)}}" target="_blank" rel="noopener">Источник медиа</a>`:''}}</section></article>`}}).join('')}}[q,cat,status].forEach(x=>x.addEventListener('input',render));render();</script></html>'''


def write_catalog(root: Path, catalog: dict[str, Any]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "CATALOG.json").write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    fields = ["asset_id", "post_id", "post_url", "local_path", "sha256", "category", "tags", "status", "rights_status"]
    with (root / "INDEX.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for item in catalog["items"]:
            writer.writerow({key: (";".join(item.get("tags", [])) if key == "tags" else item.get(key, "")) for key in fields})
    (root / "index.html").write_text(_gallery(catalog), encoding="utf-8")


def validate_catalog(root: Path, catalog: dict[str, Any] | None = None) -> list[str]:
    issues: list[str] = []
    posts_doc = _load_json(root / "source_posts.json", None)
    downloads_doc = _load_json(root / "downloads.json", None)
    reviews_doc = _load_json(root / "reviews.json", None)
    if not isinstance(posts_doc, dict) or posts_doc.get("schema_version") != 1 or posts_doc.get("source_account") != "oldmanlux" or not isinstance(posts_doc.get("coverage"), dict) or not isinstance(posts_doc.get("posts"), list):
        issues.append("source_posts.json missing or invalid required schema/account/coverage/posts")
    elif not posts_doc["posts"] and posts_doc["coverage"].get("complete") is not True:
        issues.append("empty corpus requires explicit coverage.complete=true")
    if not isinstance(downloads_doc, dict) or not isinstance(downloads_doc.get("assets"), list):
        issues.append("downloads.json missing or invalid assets list")
    if not isinstance(reviews_doc, dict) or not isinstance(reviews_doc.get("reviews"), list):
        issues.append("reviews.json missing or invalid reviews list")
    catalog = catalog if catalog is not None else _load_json(root / "CATALOG.json", {})
    if not isinstance(catalog, dict) or catalog.get("schema_version") != SCHEMA_VERSION:
        issues.append("catalog missing or unsupported schema_version")
        return issues
    expected_items = build_catalog(root).get("items", []) if isinstance(posts_doc, dict) and isinstance(downloads_doc, dict) and isinstance(reviews_doc, dict) else []
    actual_items = catalog.get("items", []) if isinstance(catalog.get("items", []), list) else []
    expected_assets = {x.get("asset_id") for x in expected_items if x.get("asset_id")}
    actual_assets = {x.get("asset_id") for x in actual_items if isinstance(x, dict) and x.get("asset_id")}
    if expected_assets != actual_assets:
        issues.append("catalog asset set does not match current downloads")
    expected_posts = {x.get("post_url") for x in expected_items if not x.get("asset_id")}
    actual_posts = {x.get("post_url") for x in actual_items if isinstance(x, dict) and not x.get("asset_id")}
    if expected_posts != actual_posts:
        issues.append("catalog post-only set does not match current source posts")
    expected_by_id = {x.get("asset_id"): x for x in expected_items if x.get("asset_id")}
    for item in actual_items:
        if isinstance(item, dict) and item.get("asset_id") in expected_by_id and item != expected_by_id[item["asset_id"]]:
            issues.append(f"catalog metadata stale: {item.get('asset_id')}")
    ids: set[str] = set()
    download_map = {str(x.get("asset_id")): x for x in downloads_doc.get("assets", []) if isinstance(x, dict)} if isinstance(downloads_doc, dict) else {}
    posts_by_url = {str(x.get("url")): x for x in posts_doc.get("posts", []) if isinstance(x, dict)} if isinstance(posts_doc, dict) else {}
    review_ids = [str(x.get("asset_id")) for x in reviews_doc.get("reviews", []) if isinstance(x, dict) and x.get("asset_id")] if isinstance(reviews_doc, dict) else []
    for aid in sorted({x for x in review_ids if review_ids.count(x) > 1}):
        issues.append(f"duplicate review asset_id: {aid}")
    download_ids = [str(x.get("asset_id")) for x in downloads_doc.get("assets", []) if isinstance(x, dict) and x.get("asset_id")] if isinstance(downloads_doc, dict) else []
    for aid in sorted({x for x in download_ids if download_ids.count(x) > 1}):
        issues.append(f"duplicate download asset_id: {aid}")
    for post in posts_by_url.values():
        if not _valid_post_source(post):
            issues.append(f"invalid source post URL: {post.get('url', '')}")
    for item in catalog.get("items", []):
        aid = item.get("asset_id", "")
        if aid:
            if aid in ids:
                issues.append(f"duplicate asset_id: {aid}")
            ids.add(aid)
            source = download_map.get(aid)
            if not source:
                issues.append(f"asset absent from downloads.json: {aid}")
                continue
            path = _safe_file(root, str(source.get("local_path", "")))
            if item.get("local_path") != source.get("local_path") or item.get("post_url") != source.get("post_url"):
                issues.append(f"catalog source binding mismatch: {aid}")
            source_post = posts_by_url.get(str(source.get("post_url", "")), {})
            local_attachment = source_post.get("source_kind") == "USER_ATTACHMENT" and source_post.get("attachment_id")
            valid_source_url = _valid_asset_media_url(source.get("source_url", ""), source_post)
            valid_download_url = _valid_asset_media_url(source.get("download_url", source.get("source_url", "")), source_post)
            if not valid_source_url or not valid_download_url:
                issues.append(f"invalid trusted media URL: {aid}")
            post = posts_by_url.get(str(source.get("post_url", "")), {})
            if not post or not _valid_asset_id(aid, _post_id(str(source.get("post_url", ""))), source.get("media_index"), post):
                issues.append(f"invalid deterministic asset_id: {aid}")
            if path is None:
                issues.append(f"path escape or invalid path: {aid}")
            elif not path.is_file():
                issues.append(f"missing file: {aid}")
            elif _sha256(path) != str(source.get("sha256", "")).lower():
                issues.append(f"hash mismatch: {aid}")
            review = next((r for r in (_load_json(root / "reviews.json", {}).get("reviews", [])) if isinstance(r, dict) and str(r.get("asset_id")) == aid), None)
            if review and str(review.get("sha256", "")).lower() != str(item.get("sha256", "")).lower():
                issues.append(f"stale review: {aid}")
        if item.get("rights_status") != UNKNOWN and item.get("rights_status") not in {"UNKNOWN", "REVIEWED", "LICENSED", "RESTRICTED", "PUBLIC_DOMAIN", "PERMISSION_GRANTED"}:
            issues.append(f"invalid rights_status: {aid or item.get('post_id')}")
    return issues


def search_catalog(catalog: dict[str, Any], query: str = "", category: str = "", tag: str = "", include_pending: bool = True) -> list[dict[str, Any]]:
    needle = query.casefold().strip()
    results = []
    for item in catalog.get("items", []):
        if not include_pending and item.get("status") != "REVIEWED":
            continue
        if category and item.get("category", "").casefold() != category.casefold():
            continue
        if tag and tag.casefold() not in {t.casefold() for t in item.get("tags", [])}:
            continue
        haystack = json.dumps([item.get(k) for k in ("text", "rendered_text", "alt", "title_ru", "summary_ru", "tags", "advice")], ensure_ascii=False).casefold()
        if needle and needle not in haystack:
            continue
        results.append(item)
    return results


def select_candidates(root: Path, catalog: dict[str, Any], asset_ids: list[str], role: str) -> dict[str, Any]:
    current_catalog = build_catalog(root)
    by_id = {item.get("asset_id"): item for item in catalog.get("items", []) if item.get("asset_id")}
    current_by_id = {item.get("asset_id"): item for item in current_catalog.get("items", []) if item.get("asset_id")}
    chosen = []
    for aid in asset_ids:
        item = by_id.get(aid)
        if item is None:
            raise ValueError(f"unknown asset_id: {aid}")
        if item != current_by_id.get(aid):
            raise ValueError(f"catalog metadata/restrictions are stale for asset_id: {aid}")
        source = next((x for x in _load_json(root / "downloads.json", {}).get("assets", []) if isinstance(x, dict) and str(x.get("asset_id")) == aid), None)
        path = _safe_file(root, str(source.get("local_path", ""))) if source else None
        if source is None or path is None or not path.is_file():
            raise ValueError(f"missing or invalid current source for asset_id: {aid}")
        current_hash = _sha256(path)
        if current_hash != str(source.get("sha256", "")).lower() or current_hash != str(item.get("sha256", "")).lower():
            raise ValueError(f"source/catalog hash is stale for asset_id: {aid}")
        if item.get("local_path") != source.get("local_path") or item.get("post_url") != source.get("post_url"):
            raise ValueError(f"source/catalog binding is stale for asset_id: {aid}")
        post = next((x for x in _load_json(root / "source_posts.json", {}).get("posts", []) if isinstance(x, dict) and x.get("url") == source.get("post_url")), {})
        if not _valid_asset_media_url(source.get("source_url", ""), post) or not _valid_asset_media_url(source.get("download_url", source.get("source_url", "")), post):
            raise ValueError(f"invalid trusted media URL for asset_id: {aid}")
        if not post or not _valid_post_source(post) or not _valid_asset_id(aid, _post_id(str(source.get("post_url", ""))), source.get("media_index"), post):
            raise ValueError(f"invalid source identity for asset_id: {aid}")
        chosen.append({"asset_id": aid, "path": item.get("local_path", ""), "sha256": item.get("sha256", ""),
                       "role": role, "source_post_url": item.get("post_url", ""),
                       "source_author": item.get("source_author", UNKNOWN), "relationship": item.get("relationship", UNKNOWN),
                       "discovered_via": item.get("discovered_via", UNKNOWN),
                       "media_source_post_url": item.get("media_source_post_url", ""),
                       "media_context_post_url": item.get("media_context_post_url", ""), "status": item.get("status"),
                       "category": item.get("category", "OTHER"), "subject_label": item.get("subject_label", UNKNOWN),
                       "rights_status": item.get("rights_status", UNKNOWN),
                       "allowed_uses": item.get("allowed_uses", []), "forbidden_uses": sorted(set(item.get("forbidden_uses", []) + ["RAW_GENERATION_ATTACHMENT"])),
                       "example_content": item.get("example_content", "UNKNOWN"),
                       "requires_positive_crop": item.get("requires_positive_crop", True),
                       "raw_generation_attachment": False, "curation_status": item.get("curation_status", "RESEARCH_ONLY")})
    return {"schema_version": 1, "kind": "CANDIDATE_SELECTION", "generation_eligible": False,
            "approval_status": "NOT_APPROVED", "notice": "Candidates require current hash verification, metadata review, and the applicable existing StoryArt approval/compatibility workflow.",
            "items": chosen}


def select_positive_candidates(root: Path, catalog: dict[str, Any], crop_ids: list[str], role: str) -> dict[str, Any]:
    """Export only current, visually accepted, pixel-exact positive fragments.

    This is still a candidate, not character/item approval. A crop's visual QA
    cannot be inferred from its hash or from a technical PASS.
    """
    from PIL import Image
    registry = _load_json(root / "POSITIVE_EXAMPLES.json", {}).get("crops", [])
    reviews = _load_json(root / "reviews.json", {}).get("reviews", [])
    selected = []
    requested = []
    for cid in crop_ids:
        matches = [c for c in registry if c.get("crop_id") == cid]
        if len(matches) != 1:
            raise ValueError(f"unknown or duplicate positive crop: {cid}")
        crop = matches[0]
        if crop.get("qa") != "PASS" or crop.get("clean_qa") != "PASS" or not crop.get("qa_evidence"):
            raise ValueError(f"positive crop lacks current visual/clean QA: {cid}")
        if crop.get("qa_bound_sha256") != crop_qa_binding(crop):
            raise ValueError(f"positive crop visual QA binding stale: {cid}")
        requested.append(crop)
    sources = select_candidates(root, catalog, list(dict.fromkeys(c['source_asset_id'] for c in requested)), role)["items"]
    sources_by_id = {item['asset_id']: item for item in sources}
    for crop in requested:
        cid = crop['crop_id']
        aid = crop.get("source_asset_id")
        base = sources_by_id[aid]
        current = [r for r in reviews if r.get("asset_id") == aid]
        digest = hashlib.sha256(json.dumps(current[0], ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest() if len(current) == 1 else ""
        if not digest or digest != crop.get("source_review_sha256") or base["sha256"] != crop.get("source_sha256"):
            raise ValueError(f"positive crop source/review stale: {cid}")
        if base.get("curation_status") == "ARCHIVE_ONLY":
            raise ValueError(f"archived source cannot be selected: {cid}")
        path = _safe_file(root, str(crop.get("path", "")))
        source = _safe_file(root, base["path"])
        if path is None or source is None or not path.is_file() or _sha256(path) != crop.get("sha256"):
            raise ValueError(f"positive crop path/hash invalid: {cid}")
        box = crop.get("box")
        with Image.open(source) as original, Image.open(path) as fragment:
            if (not isinstance(box, list) or len(box) != 4 or any(type(x) is not int for x in box)
                    or not (0 <= box[0] < box[2] <= original.width and 0 <= box[1] < box[3] <= original.height)):
                raise ValueError(f"positive crop box invalid: {cid}")
            expected = original.crop(box).convert("RGBA")
            actual = fragment.convert("RGBA")
            if actual.size != expected.size or actual.tobytes() != expected.tobytes():
                raise ValueError(f"positive fragment is not the declared source crop: {cid}")
        selected.append({**base, "crop_id": cid, "source_path": base["path"], "source_sha256": base["sha256"],
                         "path": crop["path"], "sha256": crop["sha256"], "crop_box": box,
                         "example_content": "POSITIVE_FRAGMENT", "requires_positive_crop": False,
                         "raw_generation_attachment": False, "visual_crop_qa": "PASS", "clean_crop_qa": "PASS",
                         "qa_bound_sha256": crop["qa_bound_sha256"], "qa_evidence": crop["qa_evidence"]})
    return {"schema_version": 1, "kind": "POSITIVE_FRAGMENT_CANDIDATE_SELECTION", "generation_eligible": False,
            "approval_status": "NOT_APPROVED", "items": selected,
            "notice": "Positive source fragment only. Existing subject, rights, item/character approval and scene QA remain mandatory."}


def crop_qa_binding(crop: dict[str, Any]) -> str:
    receipt = {k: crop.get(k) for k in ("source_sha256", "sha256", "box")}
    return hashlib.sha256(json.dumps(receipt, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def validate_positive_library(root: Path, catalog: dict[str, Any]) -> list[str]:
    """Recheck every crop and fail closed on unverified working-view members."""
    issues = validate_catalog(root, catalog)
    crops = _load_json(root / "POSITIVE_EXAMPLES.json", {}).get("crops", [])
    try:
        select_positive_candidates(root, catalog, [c.get("crop_id", "") for c in crops], "VALIDATION")
    except (ValueError, OSError, KeyError, TypeError) as exc:
        issues.append(f"positive registry invalid: {exc}")
    expected = {c.get("crop_id"): (c.get("source_asset_id"), c.get("path"), c.get("sha256")) for c in crops}
    for row in catalog.get("items", []):
        if (row.get("source_clean_qa") == "PASS" and row.get("source_clean_qa_sha256") == row.get("sha256")
                and row.get("curation_status") != "ARCHIVE_ONLY" and row.get("example_content") == "POSITIVE_ONLY"
                and not row.get("requires_positive_crop") and row.get("visual_review") == "PASS"):
            expected.setdefault("SOURCE:"+row["asset_id"], (row["asset_id"], row["local_path"], row["sha256"]))
    view = _load_json(root / "CURATED_ITEMS.json", None)
    if not isinstance(view, dict) or not isinstance(view.get("items"), list):
        return issues + ["missing curated working view"]
    actual = {}
    for row in view["items"]:
        key = row.get("crop_id") or "SOURCE:"+row.get("asset_id", "")
        if key in actual:
            issues.append(f"duplicate working-view member: {key}")
        actual[key] = (row.get("asset_id"), row.get("local_path"), row.get("sha256"))
    if actual != expected:
        issues.append("working-view membership/bindings differ from verified positive registry and clean source receipts")
    return issues


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("build", "validate", "validate-positive", "search", "select", "select-positive"):
        p = sub.add_parser(name)
        p.add_argument("--collection-root", type=Path, default=Path("OLDMANLUX_SOURCE_COLLECTION"))
    sub.choices["search"].add_argument("query", nargs="?", default="")
    sub.choices["search"].add_argument("--category", default="")
    sub.choices["search"].add_argument("--tag", default="")
    sub.choices["search"].add_argument("--reviewed-only", action="store_true")
    sub.choices["select"].add_argument("--asset-id", action="append", required=True)
    sub.choices["select"].add_argument("--role", required=True)
    sub.choices["select"].add_argument("--output", type=Path, required=True)
    sub.choices["select-positive"].add_argument("--crop-id", action="append", required=True)
    sub.choices["select-positive"].add_argument("--role", required=True)
    sub.choices["select-positive"].add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    root = args.collection_root
    if args.command == "build":
        catalog = build_catalog(root)
        write_catalog(root, catalog)
        print(json.dumps({"items": len(catalog["items"]), "status": "built", "coverage": catalog["coverage"]}, ensure_ascii=False))
        return 0
    catalog = _load_json(root / "CATALOG.json", {})
    if args.command == "validate-positive":
        issues = validate_positive_library(root, catalog)
        print(json.dumps({"valid": not issues, "issues": issues}, ensure_ascii=False, indent=2))
        return 0 if not issues else 1
    if args.command == "validate":
        issues = validate_catalog(root, catalog)
        print(json.dumps({"valid": not issues, "issues": issues}, ensure_ascii=False, indent=2))
        return 0 if not issues else 1
    if args.command == "search":
        print(json.dumps(search_catalog(catalog, args.query, args.category, args.tag, not args.reviewed_only), ensure_ascii=False, indent=2))
        return 0
    try:
        selection = (select_positive_candidates(root, catalog, args.crop_id, args.role)
                     if args.command == "select-positive" else select_candidates(root, catalog, args.asset_id, args.role))
    except ValueError as exc:
        parser.error(str(exc))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(selection, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"selected": len(selection["items"]), "output": str(args.output), "generation_eligible": False}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
