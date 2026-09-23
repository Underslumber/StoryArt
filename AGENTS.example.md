# StoryArt template: entrypoint rules

This is a compact routing file. The complete preserved portable requirements
are in `docs/PORTABLE_GENERATION_RULES.md`. Read only the task-relevant
sections; `docs/EFFICIENT_WORKFLOW.md` governs efficient orchestration and
evidence reuse only. It cannot relax art requirements, safety, archives,
approval, or independent QA. Explicit current user instructions override
historical stored text.

## Non-negotiable safeguards

- Preserve source images and local user data. Make derived files separately
  and retain provenance.
- Archive every generated or edited image at original format and quality in
  `GENERATION_RESULTS`, using a unique timestamped filename.
- Before substantial work, lock goal, deliverable, scope, invariants, required
  stages, and budget in `EXECUTION_GUARD.json`. Record preparation, execute
  immediately after readiness, and record visible results and final completion.
  A watchdog forbids extra preparation, never ends an active task by itself.
- For `CHARACTER_BASE`, complete and independently QA `FACE_IDENTITY`,
  `PHYSIQUE_FRONT`, `PHYSIQUE_SIDE`, `PHYSIQUE_BACK`, and
  `CHARACTER_ASSEMBLY`. A passed layer never implies another layer passed.
- Preserve approval, identity, storyline, provenance, coverage/topology, and
  source-role rules from the relevant detailed sections. Do not treat partial
  praise or an intermediate result as permanent approval.
- Generate only user-requested deliverables. After the manager prepares the
  executable plan, record `EXECUTION_STARTED` against the same
  `REFERENCE_PLAN.json` (an optional `CALL_VALIDATED` may precede it); retain
  its returned attempt id and use
  `--output-contract REQUESTED_DELIVERABLE` immediately before each ordinary
  real call.

## Efficient route

1. Classify the task and read the matching detailed rules, active guard, and
   manager documentation.
2. Before asking about style/profile, check this chat for a complete prior
   confirmation. If it is unresolved, make a bounded local metadata inventory:
   enumerate `*_PROJECT_PACK` and `*_GENERATIONS` directories, including
   git-ignored data, and inspect relevant `CHARACTER_REGISTRY.csv` files for a
   character named in the request. Do not broadly search images or treat
   `rg --files` as proof that ignored data is absent. Mention unambiguous local
   style and approved-character candidates and any `REVIEW_REQUIRED` status;
   discovery does not select or finalize them and grants no consent. Then show
   the actual contextual profile question, with the identified style plus
   recommended 90% fidelity and BODY_REFERENCE_LIBRARY first, two task-fit
   alternatives and a custom/different-style choice. Wait for the answer; do
   not ask for references already present locally. Never assume a style,
   character, or profile. Resolve target anatomy from explicit current-request
   wording or reviewed profile metadata and retain its evidence. If unknown, ask
   before showing or attaching full-body references; do not guess from names or
   images.
3. Resolve the profile before preparing or generating art. A style name alone
   does not confirm fidelity or BODY_REFERENCE_LIBRARY use. Use the permitted
   native question tool when available; otherwise visibly print the contextual
   choices from step 2 and wait. Reuse a complete same-chat confirmation; ask
   only for a missing field in a partial profile. A recommendation is not
   consent. A request for one scene produces one scene, with no helper art.
4. For substantial work, load `skills/storyart-orchestrator/SKILL.md` and
   `docs/EFFICIENT_WORKFLOW.md`; use only the smallest role set that is useful.
5. Reuse complete source-review evidence across tasks only when hashes, roles,
   style/character, covered views and limitations still apply. Inspect selected
   originals plus changed/new or uncovered sources; missing proof requires review.
6. Keep prompts short and prioritized: goal/invariants, identity, style, scene.
   Do not pile up synonyms or feed rejected outputs back as anchors.

Calibration requires an explicit user request or consent after a proposal. This
durable current rule supersedes historical automatic-calibration trigger clauses
in `docs/PORTABLE_GENERATION_RULES.md`; retain all applicable protocol and QA
requirements once authorized. After two failed attempts, identify the failed
layer and make one evidence-based recovery with the original sources; do not
loop or change scope silently.

## Image-specific model routing

Apply the image-specific authority in `docs/EFFICIENT_WORKFLOW.md`: Luna Medium
for ordinary image work; optional Luna High metadata discovery worker; Sol Low for
planning/integration and objective high-impact independent review. Normal worker count is
zero or one, maximum two independent workers. Code-development routing stays
separate. Valid hash/role/view/applicability-backed complete reviews may be
reused across tasks; inspect selected originals and changed/uncovered sources.
This preparation rule supersedes historical routine rereads, never art/QA gates.
