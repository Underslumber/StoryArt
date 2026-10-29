# External reference catalog

For the current cross-collection pool registry, search and candidate handoff,
see `docs/REFERENCE_POOLS.md` and `tools/reference_pool_manager.py`. The shared
route includes the separate pose atlas, Pinterest sketch package, costume
collection and this collection's curated working view. This manager retains
its existing source-specific and positive-fragment checks.

`tools/external_reference_catalog.py` creates a local discovery catalog from the
collection's `source_posts.json`, `downloads.json`, and `reviews.json`. It is an
additive index for external material, not an approved-reference or character
registry. Missing source files are treated as empty input and produce an honest
empty/pending catalog.

## Commands

Run through the normal Windows launcher:

```powershell
./scripts/storyart.ps1 external_reference_catalog build
./scripts/storyart.ps1 external_reference_catalog validate
./scripts/storyart.ps1 external_reference_catalog search "ткань" --category CLOTHING
./scripts/storyart.ps1 external_reference_catalog search "" --tag GARMENT:TOP --reviewed-only
./scripts/storyart.ps1 external_reference_catalog select --asset-id OLX_123_01 --role CLOTHING --output .\candidate-selection.json
```

Use `--collection-root <path>` to select a collection directory. `build` writes
`CATALOG.json`, `INDEX.csv`, and `index.html` there. The standalone gallery
filters category, review status, Unicode case-insensitive text, and tags; it
shows provenance, rights status, and a coverage warning. It loads local media
and includes no remote script dependency.

## Review and provenance rules

An asset review is applied only when its asset ID is unique and its SHA-256
matches the current downloaded file bytes. Caption-based classifications are
marked `CLASSIFIED` / `CAPTION_DERIVED` and retain `visual_review: NOT_CHECKED`;
only explicit visual `PASS` plus current non-unknown subject label/evidence can
be `REVIEWED`. Missing files, invalid paths/URLs, hash mismatches, duplicate
IDs, unsuccessful downloads, and stale reviews remain visible as non-approved
states. Unknown subject labels and rights stay
`UNKNOWN`; the catalog does not infer author ownership, a license, anatomy, or
unshown construction. Post text retains both `text` and `rendered_text`; the
latter may reflect platform translation and is not represented as original
language text.

Tags are accepted only in deterministic ASCII namespace form such as
`GARMENT:TOP` or `MATERIAL:KNIT`. The review input owns tag meanings and Russian
display titles. Text/post records without downloaded assets remain searchable
as pending posts. Caption text is retained for discovery and is not itself
visual review.

`validate` also requires all three input manifests with their required
structures. Posts authored by `oldmanlux` are accepted directly. A post by a
different author is accepted only with `relationship: REPOST` and
`discovered_via: https://x.com/oldmanlux`; its canonical original URL is kept
as the post URL, while quoted-media source and feed context are recorded
separately. No other feed is queried by this catalog. An explicitly empty corpus needs `coverage.complete: true`; this
means the acquisition intentionally recorded an empty result, not complete
account history. Source posts must use canonical `https://x.com/oldmanlux/status/<digits>` URLs (or explicit local user-attachment provenance); downloaded media URLs must use `https://pbs.twimg.com/media/` (or explicit local attachment provenance). Media dimensions, format, byte count, duplicate-content linkage, and quoted-post context are retained when supplied.

`select` rechecks the current source file and hash against both `downloads.json`
and the catalog before writing a `CANDIDATE_SELECTION` containing asset IDs, paths, hashes,
source post links, roles, restrictions, and current status. It explicitly
sets `generation_eligible: false` and `approval_status: NOT_APPROVED`; it does
not generate an item manifest or authorize physical use. Before any future
scene uses a result, reverify the selected bytes and metadata, establish the
narrow intended influence, and use the existing item/wardrobe/accessory,
reference preparation, and scene QA lifecycle as applicable. Clothing sheets
cannot transfer identity, body, or pose. Advice informs planning with its
source citation and cannot override user constraints or rewrite character
identity/profile.

## Current scope

### Corrective sheets and curated positive fragments

The current image collection has been reclassified from pixels after the user
rejected the first taxonomy. Use its current `reviews.json`; do not revive the
initial shard labels. `example_content`, `requires_positive_crop` and
`curation_status` distinguish mixed teaching sheets and archive-only material.
All raw candidate exports forbid `RAW_GENERATION_ATTACHMENT`. A category or
technical PASS does not establish a useful reference or correct anatomy.

`index_unique.html` is the curated working view. It hides archive-only sources
and replaces mixed sheets with accepted positive fragments when available.
`index_positive.html` contains only those fragments; `index.html` keeps the
complete source archive. Obviously irrelevant memes, unreadable images and
marked dense annotation are preserved outside the working view.

`select-positive --crop-id <id> --role <narrow role> --output <path>` reads
`POSITIVE_EXAMPLES.json` and rechecks the current source/review binding,
source/crop hashes, rectangular bounds, exact decoded pixel equality and
recorded visual + clean QA. Lossy resaves fail this check. A source becoming
archived or its review changing invalidates the old selection. Export stays
`NOT_APPROVED` and `generation_eligible:false`; existing subject, rights,
item/character approval, reference preparation and scene QA remain mandatory.

`validate-positive` sweeps every registered crop and the exact membership and
path/hash bindings in `CURATED_ITEMS.json`. Clean working views fail closed:
only accepted positive fragments and separately hash-bound `source_clean_qa`
whole sources are admitted; `UNKNOWN` example content stays in
`index_research.html`. Crop QA receipts bind source hash, crop hash and box.
Authoritative crop records preserve those receipts; ordinary publication must
reject missing or changed receipts rather than minting a new PASS. The local
`verify_taxonomy_v2.py` persists a reproducible full-library check report.

This utility implements local build, search, validation, and candidate export.
It does not acquire posts, prove full account-history coverage, perform visual
review, approve sources, or change production generation consumers. Coverage
claims come directly from collection input and must be reported with their
actual limits.
