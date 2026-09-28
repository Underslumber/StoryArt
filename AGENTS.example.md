# StoryArt template: entrypoint rules

This is a compact routing file. The complete preserved portable requirements
are in `docs/PORTABLE_GENERATION_RULES.md`. Read only the task-relevant
sections; `docs/EFFICIENT_WORKFLOW.md` governs orchestration and evidence
reuse for project-routed work. Archives remain optional; required scenario
selections, validation, and execution stages remain mandatory. External
platform safety limits still apply. Current user instructions override
historical stored text.

One-step edit exception: For a direct edit of a user-supplied existing image, including a mask attached in chat, use the chat attachments directly with the image-edit tool and return the result in chat. No local save, guard, plan, style questionnaire, risk report, or QA receipt is required. This direct-edit exception applies only to a direct edit of user-supplied existing imagery. For generation and CHARACTER_BASE work in StoryArt, the mandatory project route below applies. A user request authorizes the goal, not unstated choices or a scenario bypass. Preserve external platform limits.

## User-request authority for image generation

Scenario lock: preserve the established StoryArt route and step order unless
the user directly requests an exit, receives a concrete risk warning, and
confirms in the same chat. A refusal, timeout, validation error, missing
profile, or unavailable tool never authorizes a fallback generator/workflow.
Preserve request and artifact state, repair internal faults, and continue in the
scenario whenever a safe project route remains. Stop only for a concrete
external/platform/safety blocker or a missing user-only decision when no safe
authorized next step remains. Reuse the current request state and all same-chat
choices. A clear numeric menu answer is final; apply its displayed mapping and
repair internal validation mismatches without asking the user to repeat it.
When input is genuinely missing, call `request_user_input_async` once with one
question and complete options. Keep the same turn active and wait using
`clock.sleep({duration_ms: 60000})`. If it times out without an answer, repeat
only that sleep in intervals no longer than 60 seconds. While waiting, make no
commands, searches, reads, preflight, other tool calls, commentary, or final
response. When the user answers, continue with that exact selection without
asking again.

An explicit image request authorizes the work, but not unstated choices. Every generation request must run through the StoryArt scenario and project tools. Reuse style, reference, and character choices only when the user has already selected them in this chat. For missing choices, first show one compact numbered text mirror of the complete options as backup, then immediately invoke the structured native chooser (`request_user_input_async` in Default mode) with the identical question and options. The chooser is the primary selection surface; the numbered mirror is redundant recovery. Never say a menu appeared unless its tool call actually succeeded. If the chooser call fails or is unavailable, state that concrete failure and keep the numbered mirror available for a reply; never present the mirror as a native menu. A click in the card or an unambiguous matching numeric reply completes the choice; accept the first answer without reconfirmation. Options must map every displayed selection to exact call parameters. `PROJECT_STYLE_ONLY` means project style with no optional user or body-library references; named-character identity refs remain mandatory. User-attached references require matching source/path/hash/chat/message evidence, including QA-validated lineage for derived outputs. The user may explicitly choose generator-default style and no references. Resolve named project characters through the approved registry/profile and attach their approved assembly and applicable identity references. If identity or required assets cannot be resolved or attached, stop and report the blocker rather than generating a substitute. Leaving the scenario requires the user's direct request, a concrete warning, and confirmation after that warning. External platform limits cannot be bypassed.

Resolve a named character through `python tools\style_pack_manager.py resolve-character --workspace . --name "<requested name>" --json` before reporting that the approved profile is missing. This command reads approved character registries and registered identity assets only. Git-ignored generation libraries may be skipped by `rg`; do not treat a default search miss as evidence that no profile exists. Carry the returned canonical style and character ID into the project route, including common Russian case forms such as `Шанса` → `Шанс`.

If that approved profile has one bound project style, inherit its exact style name by default and do not ask the user to choose it again. If the user requests a different style or no unique style is recorded, list actual names from `style_pack_manager.py list-styles --json`; never say only “project style.” When a native chooser is invoked, wait for its structured answer without sending a follow-up message that refers to the transient menu. If it closes without an answer, treat the choice as unanswered and present it again on the next interaction.

For a new standard image-generation chooser, use one question titled `Стиль и референсы`, name the exact profile-bound style and character, and say the percentage means fidelity to that style. Show exactly: `90% стиля <STYLE> + использовать BODY_REFERENCE_LIBRARY (рекомендуемый профиль StoryArt)`; `90% стиля <STYLE>, без BODY_REFERENCE_LIBRARY`; `70% стиля <STYLE>, без BODY_REFERENCE_LIBRARY — более свободная интерпретация`. Bind these to 90/SELECTED/BODY_LIBRARY_ONLY, 90/DECLINED/APPROVED_CHARACTER_REFERENCES, and 70/DECLINED/APPROVED_CHARACTER_REFERENCES; approved identity refs stay attached in all options. Never use a percentage range or vague “profile style”/“body references” wording. For CHARACTER_BASE only, explain the library is for later physique stages and does not start calibration or pose collection. The exact displayed mapping remains authoritative; OPTION_1 has no fixed meaning for previously shown menus, which must be reused exactly.

Build a new menu with `python tools\style_pack_manager.py startup-menu-template --style-name "<exact profile style>" --character-id <CHAR_NNN> --character-name "<approved name>" --generation-purpose SCENE --json` (`CHARACTER_BASE` only for that task). Pass the returned title, question, and labels verbatim to the native chooser; retain the returned machine descriptions/mappings for preparation. Never reconstruct the labels after selecting this deterministic presenter.

In an active StoryArt workspace, every image-generation request must use the StoryArt scenario and available project tools, including brief and named-character requests. Never substitute a bare chat generator call. Check the current chat for explicit style, reference-policy, and character-identity choices; ask and wait for anything missing. A generator-only path is only for a user-supplied one-step edit or an explicitly requested standalone request outside StoryArt. If a required project tool is unavailable, try its supported recovery routes, preserve any completed artifact, and continue while a safe project route remains; report the specific blocker only when none remains.

Do not search, enumerate, inspect, or surface prior unconfirmed generations as candidate bases, references, or continuation options. Treat historical `00_PENDING` folders and manifest statuses `TEST`, `STAGING`, `REJECTED`, `DRAFT`, or any other non-approved status as ineligible, even if a copy exists in `GENERATION_RESULTS` or appeared in another chat. During an active request, inspect only that request's guard-scoped outputs when required for its own stage QA. Reuse an earlier image only when the user selects that exact image in the current chat or an authoritative project manager resolves it as an approved asset for the requested role.

Never infer a request to bypass a project rule or leave the scenario. The user must directly ask to leave it. State the concrete likely consequences and wait for the user's confirmation after the warning every time. External platform limits cannot be bypassed.

## Agent executes; user directs

The agent performs available project operations; the user gives the goal and decisions. Never instruct the user to move, place, or copy files into project folders, create files or folders, run commands, or type `READY`. Perform those operations yourself when authorized and available. Ask only for genuinely missing input or clarification. If a required source image or mask is absent, politely ask the user to attach it directly in chat; never prescribe a project-folder path.

## Non-negotiable safeguards

- Preserve source images and local user data. Make derived files separately
  and retain provenance.
- When useful to the requested task or explicitly requested, archive generated
  or project-managed edited images in `GENERATION_RESULTS`, preserving original
  format and quality. Archiving is never a prerequisite or gate.
- Before every image generation and all other substantial work, lock goal,
  deliverable, scope, invariants, required stages, and budget in
  `EXECUTION_GUARD.json`. Record preparation, execute
  immediately after readiness, and record visible results and final completion.
  A watchdog forbids extra preparation, never ends an active task by itself.
- For `CHARACTER_BASE`, complete and independently QA `FACE_IDENTITY`,
  `PHYSIQUE_FRONT`, `PHYSIQUE_SIDE`, `PHYSIQUE_BACK`, and
  `CHARACTER_ASSEMBLY`. A passed layer never implies another layer passed.
- Preserve approval, identity, storyline, provenance, coverage/topology, and
  source-role rules from the relevant detailed sections. Do not treat partial
  praise or an intermediate result as permanent approval.
- Generate only user-requested deliverables. For image generation in this
  StoryArt workspace, use the project route: bind reference-based calls to
  `REFERENCE_PLAN.json`, or use the guarded native/default route only after the
  user explicitly selects generator-default style and no references for a
  character-free request. User authorization does not remove waits for missing
  required choices.

## Efficient route

1. Classify the task and read the matching detailed rules, active guard, and
   manager documentation.
2. Style, reference-policy, and character-identity choices are required before
   generation. Reuse choices already made in the chat; ask and wait for any
   missing choice. The user may choose the generator default and no references,
   but omission is never that choice. Resolve named project characters from the
   approved registry/profile and bind their identity references to the exact
   call. Stop if identity or assets are missing; never generate a generic
   substitute. Ask for a specifically requested absent source or mask to be
   attached directly in chat.
3. In this active StoryArt workspace, generation always enters the project
   scenario and uses its tools. Leaving the scenario requires the user's direct
   request, a concrete warning, and confirmation after that warning. Use a
   direct generator-only path only for an attached-image edit or an explicitly
   requested standalone request outside StoryArt.
4. For every image-generation request, load
   `skills/storyart-orchestrator/SKILL.md` and execute its project route. For
   substantial work, also use `docs/EFFICIENT_WORKFLOW.md` and the smallest
   useful role set.
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

The StoryArt model family is selectable with `.\scripts\set-model-profile.ps1 5.6` or `.\scripts\set-model-profile.ps1 6`; see `docs/MODEL_PROFILES.md`. For future root and agent assignments, treat the family selected in `.codex/config.toml` as authoritative and use its matching `-sol` or `-luna` model for the role described below and in referenced project instructions. This supersedes fixed GPT-6 model IDs in older role tables. Existing running sessions do not change models.

Apply the image-specific authority in `docs/EFFICIENT_WORKFLOW.md`: Luna High
for ordinary image work; optional Luna High metadata discovery worker; Sol Low for
planning/integration and objective high-impact independent review. Normal worker count is
zero or one, maximum two independent workers. Code-development routing stays
separate. Valid hash/role/view/applicability-backed complete reviews may be
reused across tasks; inspect selected originals and changed/uncovered sources.
This preparation rule supersedes historical routine rereads, never art/QA gates.
