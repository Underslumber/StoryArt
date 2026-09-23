# StoryArt template: entrypoint rules

This is a compact routing file. The complete preserved portable requirements
are in `docs/PORTABLE_GENERATION_RULES.md`. Read only the task-relevant
sections; `docs/EFFICIENT_WORKFLOW.md` governs efficient orchestration and
evidence reuse only within an intentionally selected managed workflow. It does
not make archives, approvals, or QA receipts prerequisites or vetoes for an
explicit user request. External platform safety limits still apply. Current
user instructions override historical stored text.

One-step edit exception: For a direct edit of a user-supplied existing image, including a mask attached in chat, use the chat attachments directly with the image-edit tool and return the result in chat. No local save, guard, plan, style questionnaire, risk report, or QA receipt is required. This direct-edit bypass remains in effect. For generation, CHARACTER_BASE, and project work, the user-request authority below applies: internal profile, reference, risk, QA, approval, archive, and record workflows may inform work, but cannot block or delay an explicit request. Create archives and records only when useful or explicitly requested; they are never prerequisites. Preserve applicable safety requirements, with external platform safety limits still applying.

## User-request authority for image generation

An explicit user request to generate an image is sufficient authorization to proceed. Internal StoryArt guard, plan, risk, QA, archive, profile, reference, approval, and record workflows may inform execution when useful or explicitly requested, but cannot block, veto, or delay the requested work. Do not turn a request into a style/profile questionnaire or make optional records or archive copies prerequisites. If the user names no style, use the generator's native/default style. Style and references are recommendations, not required inputs; their absence does not delay generation. Ask only for a genuinely missing required deliverable field that cannot be reasonably resolved from the request. Additional checks may provide information, but do not veto the requested action. Applicable safety checks may inform execution but cannot veto an explicit request; external platform safety limits still apply. Report a real technical or platform block plainly. This rule supersedes conflicting process requirements below.

## Agent executes; user directs

The agent performs available project operations; the user gives the goal and decisions. Never instruct the user to move, place, or copy files into project folders, create files or folders, run commands, or type `READY`. Perform those operations yourself when authorized and available. Ask only for genuinely missing input or clarification. If a required source image or mask is absent, politely ask the user to attach it directly in chat; never prescribe a project-folder path.

## Non-negotiable safeguards

- Preserve source images and local user data. Make derived files separately
  and retain provenance.
- When useful to the requested task or explicitly requested, archive generated
  or project-managed edited images in `GENERATION_RESULTS`, preserving original
  format and quality. Archiving is never a prerequisite or gate.
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
- Generate only user-requested deliverables. When the optional managed
  workflow is used, bind `EXECUTION_STARTED` to its `REFERENCE_PLAN.json`; an
  optional `CALL_VALIDATED` may precede it. Never require this workflow to
  authorize or delay a direct explicit request.

## Efficient route

1. Classify the task and read the matching detailed rules, active guard, and
   manager documentation.
2. Style and reference choices are optional recommendations. If the user
   names no style, use the generator's native/default style and proceed without
   waiting. If useful, mention 90% fidelity and BODY_REFERENCE_LIBRARY as
   recommendations; do not inventory folders or ask a startup questionnaire to
   unlock generation. Ask only for a genuinely missing required field. If a
   specifically requested source or mask is absent, ask the user to attach it
   directly in chat. Use available approved-character context when relevant;
   do not guess identity or anatomy.
3. The manager and guard sequence is an optional managed workflow. Use it only
   when the user explicitly requests it or it is useful and does not delay the
   requested result. It cannot turn an explicit image request into a wait for
   profile confirmation or internal records.
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
