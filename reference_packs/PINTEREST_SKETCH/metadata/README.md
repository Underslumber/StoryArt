# Pinterest sketch reference staging

This is a task-local staging tool. It does not modify `BODY_REFERENCE_LIBRARY`, the active `POSE_ATLAS`, character profiles, or production registries. Every catalog row has `generation_eligible: false`; a manual PASS only makes a row selected for this staging collection.

## Run

Use the configured StoryArt Python with Pillow:

```powershell
.\.venv\Scripts\python.exe .\work\pinterest-sketch-20261029\pipeline.py download --sources .\work\pinterest-sketch-20261029\sources.json
.\.venv\Scripts\python.exe .\work\pinterest-sketch-20261029\pipeline.py contact-sheets
.\.venv\Scripts\python.exe .\work\pinterest-sketch-20261029\pipeline.py build
.\.venv\Scripts\python.exe .\work\pinterest-sketch-20261029\pipeline.py validate
```

`download` accepts a JSON array or `{ "sources": [...] }`. Each supplied row has `{ "id", "title", "alt", "urls": [...], "via" }`. It contacts only explicitly supplied HTTPS URLs on the exact `i.pinimg.com` host, refuses redirects, tries `/originals/` first and otherwise the largest supplied size URL, uses at most four concurrent workers and 25 seconds per request, and caps one file at 40 MiB. No Pinterest search, browser, URL guessing, or adjacent-pin discovery occurs here. The harvest step must provide observed candidate URLs.

Downloads are saved under `originals/`. `downloads.json` records source ID, source URL/provenance, dimensions, format, timestamp, local path and SHA-256. An existing file is reused only when source ID, supplied URL and current file hash all match its prior record. Exact duplicate bytes are retained and disclosed; they are not destructively deduplicated. `perceptual_candidates.json` contains dHash near-duplicate pairs for human inspection only.

`contact-sheets/` contains numbered pages of at most 30 originals. It is for review only; source originals are never resized or overwritten. Fill `review.json` manually after viewing images. Every PASS must contain `source_sha256` equal to the exact original bytes being reviewed, plus reviewer and evidence. Missing or stale review hashes reset the catalog decision to `NOT_REVIEWED` and quarantined. The tool does not infer semantic labels from title, alt text, or URL. Start with:

```json
{
  "schema_version": 1,
  "reviews": {
    "source-id": {
      "review_status": "PASS",
      "source_sha256": "exact sha256 from downloads.json",
      "reviewer": "reviewer identifier",
      "review_evidence": "what was visually checked",
      "subject_label_evidence": "visual evidence for this label",
      "contact_evidence": "when applicable, describe the visible contact between digits/objects",
      "media_type": "SKETCH",
      "subject_label": "MALE",
      "kind": "SINGLE_POSE",
      "groups": ["COMBAT", "MAGIC"],
      "tags": ["WEAPON", "CAMERA_ANGLE", "HAND_GESTURE"],
      "notes": "optional"
    }
  }
}
```

Allowed subject labels are `MALE`, `FEMALE`, `NO_SUBJECT`, `UNKNOWN`, `MIXED`; media types are `SKETCH`, `PHOTO`, `UNKNOWN`; kinds are `SINGLE_POSE`, `MULTI_POSE_SHEET`, `GROUP_SCENE`, `UNKNOWN`. Keep photos excluded from selection. Unknown/mixed items remain quarantined from subject-source use. `GROUP_SCENE` and eligible isolated-hand references may be selected into this staging collection only; they remain quarantined and generation-ineligible. A `MIXED` label is accepted only for `GROUP_SCENE` with explicit `subject_label_evidence`. An isolated hand may be staged with `UNKNOWN` only when `subject_label_evidence` states `subject not determinable from isolated hand`; never label an isolated hand `NO_SUBJECT`. Both cases require independent visual review evidence and hand/group tags. `groups` can include multiple semantic groups, for example `MAGIC`, `COMBAT`, `WEAPON`, `ANIMAL`, `COMPLEX_POSE`, `CAMERA_ANGLE`, `HAND_GESTURE`, and `FINGER_GESTURE`; an asset appears in each group output. `tags` retain any uppercase ASCII namespace tag matching `[A-Z][A-Z0-9_:]{1,64}`, such as `HAND:SNAP_PREPARATION`, `HAND:SNAP_RELEASE`, `HAND:THUMB_MIDDLE_CONTACT`, `HAND:THUMB_INDEX_CONTACT`, `HAND:PINCH`, `FINGERS:CROSSED`, `FINGERS:INTERLOCKED`, `GESTURE:MUDRA_LOTUS`, or `GESTURE:POINTING`. Record what visually contacts what in `contact_evidence` for clicks, snaps, pinches, and related gestures. Invalid tags cause an explicit build error rather than disappearing. Do not use captions as evidence for a subject label or visual classification.

For an animal rider, hug, or other group scene where human sex cannot be established, keep `subject_label: UNKNOWN` with explicit `subject_label_evidence`. The item may be collection-selected under `GROUP_SCENE`, with `quarantined: true`, `subject_source_eligible: false`, and `generation_eligible: false`. Never relabel it `MIXED` to make it selectable; `MIXED` means multiple subjects with evidence, not unknown sex.

## Multi-pose sheets

Mark a visually confirmed sketch sheet `MULTI_POSE_SHEET`; it remains visible in the catalog but is never counted as a selected single reference. For each desired panel, add a crop entry under that sheet's review record. Bounding boxes use pixel coordinates `[left, top, right, bottom]` in the unchanged parent original. Crop semantic review is explicit and independent:

```json
"crops": [
  {
    "id": "source-id_pose_01",
    "bbox": [12, 18, 640, 900],
    "component_mask_path": "masks/source-id_pose_01.png",
    "component_mask_sha256": "sha256 of the exact mask bytes",
    "review_status": "PASS",
      "source_sha256": "the exact parent original hash",
      "parent_sha256": "the exact parent original hash",
    "reviewer": "reviewer identifier",
    "review_evidence": "single figure fully visible; sketch confirmed",
    "contact_evidence": "thumb pad touches middle fingertip before release",
    "media_type": "SKETCH",
    "subject_label": "MALE",
    "kind": "SINGLE_POSE",
    "groups": ["COMBAT"],
    "tags": ["WEAPON", "FINGER_GESTURE"]
  }
]
```

`build` creates crops as new PNG derivatives under `crops/`, recording parent ID/hash, crop box, crop hash, parent review hash, and a crop review binding to the exact parent hash plus bbox. The parent sheet remains untouched. Only reviewed single-pose or group-scene crop derivatives can be selected. A crop needs its own explicit PASS and evidence; parent review does not transfer to it. Invalid bounding boxes fail the build. A crop whose `source_sha256`, `parent_sha256`, or bbox does not match is quarantined. Rectangular bbox crops may include neighboring ink; where components touch/overlap, provide an optional `component_mask_path` and `component_mask_sha256` in the crop review. The mask must be an in-root, current-hash, binary (0/255) grayscale PNG whose dimensions exactly equal bbox width and height. The pipeline composites the unchanged source bbox against white using that mask, writes a derivative, and records `transform: BBOX_COMPONENT_ISOLATION`, mask path/hash and source bbox. It does not redraw or alter the original. Missing, stale, external, nonbinary or wrong-size masks reject the crop. Without a mask the transform is `BBOX_CROP`. Validation repeats the exact composite and checks the derivative bytes.

## Outputs and validation

`build` writes `catalog.json`, `catalog.csv`, `catalog.html`, `perceptual_candidates.json`, `groups.json`, `group_scenes.json`, and crop derivatives from the current `downloads.json` and `review.json`. Catalog entries retain provenance and original bytes; `catalog.html` hides unreviewed and quarantined records by default, with filters for semantic tags/groups, media type, subject and kind. Selected group/hand intake is visibly marked as quarantined; its image preview loads only after an explicit click. Normal selected references load lazily when visible. The 1000 selected-item cap counts collection-selected images and crops, never source sheets. If selection exceeds 1000, the build errors before writing catalog/crop outputs; it never silently truncates. Exact duplicates remain in inventory, and the best reviewed eligible occurrence is selected deterministically. Every record remains staging-only and generation-ineligible; `subject_source_eligible` separately marks whether subject metadata is suitable for later source consideration.

`download` atomically checkpoints every 25 completed URL rows; failed attempts live separately in `download_failures.json`. `validate` rechecks current hashes, exact review-to-source bindings, crop parent/review hash/box and reconstructed crop bytes, manual review completeness, photos/quarantine, duplicate selected content, and the 1000-item cap. A validation PASS does not grant author approval or production eligibility. Preserve manual review inputs and originals as user data.
