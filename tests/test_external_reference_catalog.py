import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

from tools.external_reference_catalog import (
    build_catalog,
    search_catalog,
    select_candidates,
    select_positive_candidates,
    crop_qa_binding,
    validate_positive_library,
    validate_catalog,
    write_catalog,
)


def positive_fixture(root):
    from PIL import Image
    fixture_collection(root)
    source = root / "media" / "top.bin"
    Image.new("RGB", (12, 8), "green").save(source, format="PNG")
    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    for filename, key in (("downloads.json", "assets"), ("reviews.json", "reviews")):
        doc = json.loads((root / filename).read_text("utf-8"))
        doc[key][0]["sha256"] = sha
        if key == "reviews":
            doc[key][0].update(example_content="MIXED_CORRECT_INCORRECT", requires_positive_crop=True,
                               curation_status="RESEARCH_ONLY")
            review = doc[key][0]
        (root / filename).write_text(json.dumps(doc), encoding="utf-8")
    dest = root / "media" / "positive.png"
    with Image.open(source) as image:
        image.crop((6, 0, 12, 8)).save(dest)
    crop = {"crop_id": "POS_123_01", "source_asset_id": "OLX_123_01", "source_sha256": sha,
            "source_review_sha256": hashlib.sha256(json.dumps(review, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest(),
            "path": "media/positive.png", "sha256": hashlib.sha256(dest.read_bytes()).hexdigest(),
            "box": [6, 0, 12, 8], "qa": "PASS", "clean_qa": "PASS", "qa_evidence": "human inspected exact fragment"}
    crop['qa_bound_sha256'] = crop_qa_binding(crop)
    (root / "POSITIVE_EXAMPLES.json").write_text(json.dumps({"crops": [crop]}), encoding="utf-8")
    return crop


def test_positive_export_is_current_pixel_exact_and_non_approving(tmp_path):
    positive_fixture(tmp_path)
    catalog = build_catalog(tmp_path)
    raw = select_candidates(tmp_path, catalog, ["OLX_123_01"], "CLOTHING")["items"][0]
    assert raw["requires_positive_crop"] is True
    assert "RAW_GENERATION_ATTACHMENT" in raw["forbidden_uses"]
    result = select_positive_candidates(tmp_path, catalog, ["POS_123_01"], "CLOTHING")
    assert result["generation_eligible"] is False
    assert result["approval_status"] == "NOT_APPROVED"
    assert result["items"][0]["path"] == "media/positive.png"
    assert result["items"][0]["source_path"] == "media/top.bin"


def test_positive_export_rejects_changed_pixels_even_with_updated_hash(tmp_path):
    import pytest
    from PIL import Image
    crop = positive_fixture(tmp_path)
    dest = tmp_path / crop["path"]
    Image.new("RGB", (6, 8), "red").save(dest)
    crop["sha256"] = hashlib.sha256(dest.read_bytes()).hexdigest()
    crop['qa_bound_sha256'] = crop_qa_binding(crop)
    (tmp_path / "POSITIVE_EXAMPLES.json").write_text(json.dumps({"crops": [crop]}), encoding="utf-8")
    with pytest.raises(ValueError, match="not the declared source crop"):
        select_positive_candidates(tmp_path, build_catalog(tmp_path), [crop["crop_id"]], "CLOTHING")


def test_positive_export_rejects_missing_clean_qa_and_stale_source_review(tmp_path):
    import pytest
    crop = positive_fixture(tmp_path)
    crop["clean_qa"] = "NOT_CHECKED"
    (tmp_path / "POSITIVE_EXAMPLES.json").write_text(json.dumps({"crops": [crop]}), encoding="utf-8")
    with pytest.raises(ValueError, match="visual/clean QA"):
        select_positive_candidates(tmp_path, build_catalog(tmp_path), [crop["crop_id"]], "CLOTHING")


def test_positive_qa_receipt_and_working_membership_fail_closed(tmp_path):
    import pytest
    crop = positive_fixture(tmp_path)
    catalog = build_catalog(tmp_path)
    view = {'items':[{'crop_id':crop['crop_id'],'asset_id':crop['source_asset_id'],
                       'local_path':crop['path'],'sha256':crop['sha256']}]}
    (tmp_path/'CURATED_ITEMS.json').write_text(json.dumps(view),encoding='utf-8')
    assert validate_positive_library(tmp_path,catalog)==[]
    view['items'].append({'asset_id':'OLX_123_01','local_path':'media/top.bin','sha256':crop['source_sha256']})
    (tmp_path/'CURATED_ITEMS.json').write_text(json.dumps(view),encoding='utf-8')
    assert any('membership' in s for s in validate_positive_library(tmp_path,catalog))
    crop['box']=[0,0,6,8]
    (tmp_path/'POSITIVE_EXAMPLES.json').write_text(json.dumps({'crops':[crop]}),encoding='utf-8')
    with pytest.raises(ValueError,match='QA binding stale'):
        select_positive_candidates(tmp_path,catalog,[crop['crop_id']],'CLOTHING')
    crop['box']=[6,0,12,8]
    crop['qa_bound_sha256']=crop_qa_binding(crop)
    crop["clean_qa"] = "PASS"
    (tmp_path / "POSITIVE_EXAMPLES.json").write_text(json.dumps({"crops": [crop]}), encoding="utf-8")
    doc = json.loads((tmp_path / "reviews.json").read_text("utf-8"))
    doc["reviews"][0]["curation_status"] = "ARCHIVE_ONLY"
    (tmp_path / "reviews.json").write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(ValueError, match="source/review stale"):
        select_positive_candidates(tmp_path, build_catalog(tmp_path), [crop["crop_id"]], "CLOTHING")


def fixture_collection(root: Path) -> None:
    (root / "media").mkdir(parents=True)
    content = b"test-image-bytes"
    (root / "media" / "top.bin").write_bytes(content)
    sha = hashlib.sha256(content).hexdigest()
    (root / "source_posts.json").write_text(json.dumps({
        "schema_version": 1, "source_account": "oldmanlux",
        "coverage": {"observed_posts": 1, "complete": False},
        "posts": [{"url": "https://x.com/oldmanlux/status/123", "text": "Совет: ткань и посадка",
                   "rendered_text": "Совет: ткань и посадка", "time": "2026-01-01",
                   "images": [{"src": "https://pbs.twimg.com/media/a.jpg", "alt": "топ"}], "links": [],
                   "has_video": False, "observed_at": "2026-10-29"}],
    }), encoding="utf-8")
    (root / "downloads.json").write_text(json.dumps({"assets": [{
        "asset_id": "OLX_123_01", "post_url": "https://x.com/oldmanlux/status/123", "media_index": 1,
        "source_url": "https://pbs.twimg.com/media/a.jpg", "download_url": "https://pbs.twimg.com/media/a.jpg",
        "local_path": "media/top.bin", "sha256": sha, "width": 1, "height": 1,
        "bytes": len(content), "download_status": "DOWNLOADED",
    }]}), encoding="utf-8")
    (root / "reviews.json").write_text(json.dumps({"reviews": [{
        "asset_id": "OLX_123_01", "sha256": sha, "category": "CLOTHING", "tags": ["GARMENT:TOP"],
        "title_ru": "Топ", "summary_ru": "Пример посадки", "subject_label": "FEMALE",
        "subject_label_evidence": "REVIEWED", "visual_review": "PASS", "allowed_uses": ["CLOTHING"],
        "classification_basis": "VISUAL",
        "forbidden_uses": ["IDENTITY"], "advice": [{"text": "Сверить складки", "source_url": "https://x.com/oldmanlux/status/123"}],
        "rights_status": "UNKNOWN",
    }]}), encoding="utf-8")


def test_build_search_selection_and_gallery_are_non_approving(tmp_path):
    fixture_collection(tmp_path)
    catalog = build_catalog(tmp_path)
    item = catalog["items"][0]
    assert item["status"] == "REVIEWED"
    assert item["rights_status"] == "UNKNOWN"
    assert search_catalog(catalog, "ТКАНЬ", category="CLOTHING")[0]["asset_id"] == "OLX_123_01"
    selected = select_candidates(tmp_path, catalog, ["OLX_123_01"], "CLOTHING")
    assert selected["generation_eligible"] is False
    assert selected["approval_status"] == "NOT_APPROVED"
    write_catalog(tmp_path, catalog)
    gallery = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "Coverage reflects" in gallery
    assert "<script src=" not in gallery
    assert validate_catalog(tmp_path, catalog) == []


def test_missing_review_and_empty_inputs_stay_pending(tmp_path):
    fixture_collection(tmp_path)
    (tmp_path / "reviews.json").write_text('{"reviews": []}', encoding="utf-8")
    item = build_catalog(tmp_path)["items"][0]
    assert item["status"] == "PENDING_REVIEW"
    assert item["subject_label"] == "UNKNOWN"
    empty = build_catalog(tmp_path / "absent")
    assert empty["items"] == []
    assert "does not prove" in empty["coverage_warning"]


def test_hash_bound_review_and_path_traversal_fail_closed(tmp_path):
    fixture_collection(tmp_path)
    downloads = json.loads((tmp_path / "downloads.json").read_text(encoding="utf-8"))
    downloads["assets"][0]["local_path"] = "../outside.bin"
    (tmp_path / "downloads.json").write_text(json.dumps(downloads), encoding="utf-8")
    catalog = build_catalog(tmp_path)
    assert catalog["items"][0]["status"] == "INVALID_PATH"
    assert any("path escape" in issue for issue in validate_catalog(tmp_path, catalog))


def test_stale_review_is_not_applied(tmp_path):
    fixture_collection(tmp_path)
    reviews = json.loads((tmp_path / "reviews.json").read_text(encoding="utf-8"))
    reviews["reviews"][0]["sha256"] = "0" * 64
    (tmp_path / "reviews.json").write_text(json.dumps(reviews), encoding="utf-8")
    item = build_catalog(tmp_path)["items"][0]
    assert item["status"] == "PENDING_REVIEW"
    assert item["category"] == "OTHER"


def test_caption_classification_does_not_claim_visual_review(tmp_path):
    fixture_collection(tmp_path)
    reviews = json.loads((tmp_path / "reviews.json").read_text(encoding="utf-8"))
    reviews["reviews"][0].update({"classification_basis": "CAPTION", "visual_review": "NOT_CHECKED"})
    (tmp_path / "reviews.json").write_text(json.dumps(reviews), encoding="utf-8")
    item = build_catalog(tmp_path)["items"][0]
    assert item["status"] == "CLASSIFIED"
    assert item["classification_origin"] == "CAPTION_DERIVED"
    assert item["visual_review"] == "NOT_CHECKED"
    assert item["subject_label"] == "UNKNOWN"


def test_gallery_rejects_corrupted_urls_and_paths(tmp_path):
    fixture_collection(tmp_path)
    catalog = build_catalog(tmp_path)
    item = catalog["items"][0]
    item["local_path"] = "javascript:alert(1)"
    item["post_url"] = "javascript:alert(2)"
    item["source_url"] = "https://evil.example/x.jpg"
    from tools.external_reference_catalog import _gallery
    gallery = _gallery(catalog)
    assert 'href="javascript:' not in gallery
    assert 'src="javascript:' not in gallery
    assert 'href="https://evil.example' not in gallery
    item["local_path"] = "media/%2e%2e%2fsecret.jpg"
    gallery = _gallery(catalog)
    assert 'href="media/%2e%2e' not in gallery


def test_duplicate_bytes_and_duplicate_ids_are_diagnosed(tmp_path):
    fixture_collection(tmp_path)
    downloads = json.loads((tmp_path / "downloads.json").read_text(encoding="utf-8"))
    duplicate = dict(downloads["assets"][0])
    duplicate["asset_id"] = "OLX_123_02"
    duplicate["media_index"] = 2
    downloads["assets"].append(duplicate)
    (tmp_path / "downloads.json").write_text(json.dumps(downloads), encoding="utf-8")
    catalog = build_catalog(tmp_path)
    assert catalog["items"][1]["duplicate_of"] == "OLX_123_01"
    downloads["assets"].append(dict(duplicate))
    (tmp_path / "downloads.json").write_text(json.dumps(downloads), encoding="utf-8")
    assert any("duplicate download asset_id" in issue for issue in validate_catalog(tmp_path, build_catalog(tmp_path)))


def test_selection_revalidates_current_file_hash_and_catalog_binding(tmp_path):
    fixture_collection(tmp_path)
    catalog = build_catalog(tmp_path)
    (tmp_path / "media" / "top.bin").write_bytes(b"changed bytes")
    try:
        select_candidates(tmp_path, catalog, ["OLX_123_01"], "CLOTHING")
    except ValueError as exc:
        assert "stale" in str(exc)
    else:
        raise AssertionError("stale catalog selection was exported")


def test_invalid_source_urls_and_missing_inputs_are_not_valid(tmp_path):
    fixture_collection(tmp_path)
    posts = json.loads((tmp_path / "source_posts.json").read_text(encoding="utf-8"))
    posts["posts"][0]["url"] = "https://evil.example/oldmanlux/status/123"
    (tmp_path / "source_posts.json").write_text(json.dumps(posts), encoding="utf-8")
    assert any("invalid source post URL" in issue for issue in validate_catalog(tmp_path, build_catalog(tmp_path)))
    assert any("source_posts.json missing" in issue for issue in validate_catalog(tmp_path / "missing", {}))


def test_unrelated_external_post_rejected_but_feed_repost_with_context_is_preserved(tmp_path):
    fixture_collection(tmp_path)
    posts = json.loads((tmp_path / "source_posts.json").read_text(encoding="utf-8"))
    original = posts["posts"][0]
    original.update({"url": "https://x.com/artist_handle/status/456", "source_author": "artist_handle",
                     "relationship": "REPOST", "discovered_via": "https://x.com/oldmanlux"})
    original["images"][0]["source_post_url"] = "https://x.com/artist_handle/status/456"
    (tmp_path / "source_posts.json").write_text(json.dumps(posts), encoding="utf-8")
    downloads = json.loads((tmp_path / "downloads.json").read_text(encoding="utf-8"))
    asset = downloads["assets"][0]
    asset.update({"asset_id": "OLX_456_01", "post_url": original["url"], "media_context_post_url": "https://x.com/oldmanlux/status/789"})
    (tmp_path / "downloads.json").write_text(json.dumps(downloads), encoding="utf-8")
    reviews = json.loads((tmp_path / "reviews.json").read_text(encoding="utf-8"))
    reviews["reviews"][0]["asset_id"] = "OLX_456_01"
    (tmp_path / "reviews.json").write_text(json.dumps(reviews), encoding="utf-8")
    item = build_catalog(tmp_path)["items"][0]
    assert item["post_url"] == "https://x.com/artist_handle/status/456"
    assert item["source_author"] == "artist_handle"
    assert item["relationship"] == "REPOST"
    assert item["discovered_via"] == "https://x.com/oldmanlux"
    assert item["media_source_post_url"] == "https://x.com/artist_handle/status/456"
    assert item["media_context_post_url"] == "https://x.com/oldmanlux/status/789"


def test_external_post_without_feed_repost_provenance_is_rejected(tmp_path):
    fixture_collection(tmp_path)
    posts = json.loads((tmp_path / "source_posts.json").read_text(encoding="utf-8"))
    posts["posts"][0]["url"] = "https://x.com/someone_else/status/123"
    (tmp_path / "source_posts.json").write_text(json.dumps(posts), encoding="utf-8")
    catalog = build_catalog(tmp_path)
    assert catalog["items"][0]["status"] == "INVALID_POST_URL"


def test_select_rejects_changed_review_permissions(tmp_path):
    fixture_collection(tmp_path)
    catalog = build_catalog(tmp_path)
    reviews = json.loads((tmp_path / "reviews.json").read_text(encoding="utf-8"))
    reviews["reviews"][0].update({"visual_review": "FAIL", "allowed_uses": [], "forbidden_uses": ["CLOTHING"]})
    (tmp_path / "reviews.json").write_text(json.dumps(reviews), encoding="utf-8")
    try:
        select_candidates(tmp_path, catalog, ["OLX_123_01"], "CLOTHING")
    except ValueError as exc:
        assert "metadata/restrictions are stale" in str(exc)
    else:
        raise AssertionError("stale review permissions were exported")


def test_validate_rejects_catalog_missing_current_assets(tmp_path):
    fixture_collection(tmp_path)
    catalog = build_catalog(tmp_path)
    catalog["items"] = []
    issues = validate_catalog(tmp_path, catalog)
    assert any("asset set does not match" in issue for issue in issues)


def test_user_attachment_local_provenance_builds_and_validates(tmp_path):
    (tmp_path / "media").mkdir(parents=True)
    blob = b"attachment-bytes"
    (tmp_path / "media" / "attachment.bin").write_bytes(blob)
    sha = hashlib.sha256(blob).hexdigest()
    post = {"url": "local://attachment/OLX_USER_TOPS_01", "source_kind": "USER_ATTACHMENT",
            "attachment_id": "OLX_USER_TOPS_01", "images": [{"src": "local://attachment/OLX_USER_TOPS_01"}]}
    (tmp_path / "source_posts.json").write_text(json.dumps({"schema_version": 1, "source_account": "oldmanlux",
        "coverage": {"complete": True}, "posts": [post]}), encoding="utf-8")
    (tmp_path / "downloads.json").write_text(json.dumps({"assets": [{"asset_id": "OLX_USER_TOPS_01",
        "post_url": post["url"], "media_index": 1, "source_url": post["url"], "download_url": post["url"],
        "local_path": "media/attachment.bin", "sha256": sha, "download_status": "DOWNLOADED"}]}), encoding="utf-8")
    (tmp_path / "reviews.json").write_text('{"reviews": []}', encoding="utf-8")
    catalog = build_catalog(tmp_path)
    assert catalog["items"][0]["status"] == "PENDING_REVIEW"
    assert not validate_catalog(tmp_path, catalog)


def test_gallery_javascript_parses_when_node_is_available(tmp_path):
    node = shutil.which("node")
    if not node:
        import pytest
        pytest.skip("Node.js is unavailable")
    fixture_collection(tmp_path)
    from tools.external_reference_catalog import _gallery
    gallery = _gallery(build_catalog(tmp_path))
    scripts = re.findall(r"<script(?: [^>]*)?>(.*?)</script>", gallery, flags=re.DOTALL)
    script_path = tmp_path / "gallery.js"
    script_path.write_text(scripts[-1], encoding="utf-8")
    result = subprocess.run([node, "--check", str(script_path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
