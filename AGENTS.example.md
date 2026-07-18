# StoryArt agent instructions

- Prefer minimal, non-breaking changes and avoid unnecessary abstractions.
- Treat every `*_PROJECT_PACK`, `*_GENERATIONS`, `BODY_REFERENCE_LIBRARY`, `GENERATION_RESULTS`, source collection, and image as local user data that must not be committed. The only exception is a user-approved lightweight README preview under `docs/assets/previews`.
- Preserve source images unchanged. Create derivatives as new files and record their provenance in the relevant manifest.
- Use `tools/style_pack_manager.py` for style-pack creation, ingestion, discovery, generation records, approvals, and validation.
- Use `tools/body_reference_manager.py` to append to an existing body-reference library; never rebuild or renumber the library.
- Run `tools/generation_risk_assessor.py` before an image-generator call and store the resulting assessment with the local request.
- Keep generated or edited images in the local `GENERATION_RESULTS` archive as well as their requested destination.
- Before finishing a code change, run `python -m unittest discover -s tests -v` and compile the changed Python tools.
