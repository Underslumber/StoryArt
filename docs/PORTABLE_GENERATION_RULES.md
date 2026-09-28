# StoryArt agent instructions

## User-request authority for image generation

### Current-chat choices and active folder

The approved profile fixes the STYLE NAME only, never fidelity or BODY_REFERENCE_LIBRARY. In a new chat, show the standard Стиль и референсы chooser before source selection/preparation unless both choices were explicitly made in this chat. Do not inherit choices from another chat.

For image assets, access only approved project folders and ONE active request folder bound to this chat. Never enumerate, search, open or inspect other pending/unapproved folders or historical TEST, DRAFT and REJECTED outputs. Similar scene wording is not continuation authority. A new chat uses a fresh request; previous attempts, failures and budgets never transfer. Resolve reusable assets only from approved registries.


For ordinary generation, follow the minimal route in `EFFICIENT_WORKFLOW.md`:
reuse resolved choices and selected-source evidence, prepare once, then record
`EXECUTION_STARTED` and call built-in `image_gen` with the prepared inputs.
No separate StoryArt executor, availability precheck, routine CALL_VALIDATED,
full candidate-pool inspection or orchestration state without workers is needed.
The scenario's generator restriction prohibits skipping its preparation.

Every confirmed character-profile update must apply by default in subsequent
requests. Use the shared `confirm-profile` lifecycle for arbitrary structured
structured data and active visual defaults; image approvals publish through that
same lifecycle. The agent saves the update after the user's confirmation,
without another confirmation or manual file work by the user. Resolve the
effective confirmed revision before selecting scene references. Include its
profile facts in the executable prompt and applicable visual defaults in actual
attachments. Scene-only overrides do not change persistent defaults; an
alternative-only asset does not change the active selection. Unconfirmed edits
must not replace the confirmed revision. Bind revision, facts and asset hashes
to the exact call and rebuild stale plans without repeating the user's decision.

The established StoryArt scenario and its existing step order are mandatory.
Never bypass a step or substitute a generator/workflow because of a refusal,
timeout, validator error, missing profile, or unavailable tool; report the
concrete blocker. Exit only on the user's direct request, followed by a
concrete warning and the user's confirmation in the same chat. Restore and
reuse the current request's state and all same-chat choices. A clear numeric
answer is final; apply its exact displayed mapping and fix internal validation
errors without asking the user to repeat it. If input is missing, call
`request_user_input_async` once with one question and complete options. Keep the
same turn active and wait using `clock.sleep({duration_ms: 60000})`. If it times
out without an answer, repeat only that sleep in intervals no longer than 60
seconds. While waiting, do no commands, searches, reads, preflight, other tools,
commentary, or final response. Continue the same step when the user answers.

An explicit image request authorizes the requested work, but not unstated image choices. Every image request must follow the StoryArt scenario and its project tools. Before a generator call, reuse only style and reference choices the user has already made in this chat; ask and wait for any missing choice. The user may explicitly choose the generator's default style and no references. For a named project character, resolve the approved registry/profile and attach the approved assembly plus applicable identity references. An explicitly selected GENERATOR_DEFAULT style still uses the reference-bound route without project STYLE slots; the no-reference IMAGE_GENERATION_NATIVE_DEFAULT lane is only for character-free requests. If the character or required assets cannot be resolved or attached, stop and state the blocker instead of generating a substitute. Leaving the scenario requires the user's direct request, a concrete warning, and the user's confirmation after that warning. External platform limits cannot be bypassed.

Before reporting a named character's profile missing, run `python tools\style_pack_manager.py resolve-character --workspace . --name "<requested name>" --json`. It resolves only `APPROVED` registry rows and their registered identity assets; it does not search historical generations. Do not use a default `rg` search as proof of absence because Git-ignored generation libraries may be skipped. Carry its canonical style and character ID into the scenario; common Russian case forms such as `Шанса` resolve to `Шанс`. Report a blocker only from a resolver miss or a concrete resolver/tool error.

If the approved profile binds the character to one project style, inherit only that exact style name; fidelity and BODY_REFERENCE_LIBRARY still require the current-chat chooser. If the user explicitly asks for another style or the profile has no unique style, list actual names from `style_pack_manager.py list-styles --json` and ask with those names, never with the vague label “project style.” A native chooser must be the last user-visible action in its turn: wait for its structured answer. If it closes without an answer, treat the choice as unanswered and present it again on the next interaction.

For a new standard image-generation chooser, use one question titled `Стиль и референсы`; name the exact profile-bound style and character, and explain that the percentage is fidelity to that style. Show exactly three presets: `90% стиля <STYLE> + использовать BODY_REFERENCE_LIBRARY (рекомендуемый профиль StoryArt)`; `90% стиля <STYLE>, без BODY_REFERENCE_LIBRARY`; `70% стиля <STYLE>, без BODY_REFERENCE_LIBRARY — более свободная интерпретация`. Bind these respectively to 90/SELECTED/BODY_LIBRARY_ONLY, 90/DECLINED/APPROVED_CHARACTER_REFERENCES, and 70/DECLINED/APPROVED_CHARACTER_REFERENCES. Approved identity references stay attached in every option. Do not use a percentage range or replace the exact style/library decision with vague wording. For CHARACTER_BASE only, explain the library applies to later physique stages and does not start calibration or pose collection. OPTION_1 has no fixed meaning for an already shown menu: preserve that exact mapping and never reject or reconfirm a valid choice against an old default.

Build each new profile-bound menu with `python tools\style_pack_manager.py startup-menu-template --style-name "<exact profile style>" --character-id <CHAR_NNN> --character-name "<approved name>" --generation-purpose SCENE --json` (use `CHARACTER_BASE` only for that task). Pass the returned title, question, and labels verbatim to the native chooser and retain the returned machine descriptions/mappings for preparation. This reuses the established steps and resolves no new user decision.

Do not search, enumerate, inspect, or surface prior unconfirmed generations as candidate bases, references, or continuation options. Treat historical `00_PENDING` folders and manifest statuses `TEST`, `STAGING`, `REJECTED`, `DRAFT`, or any other non-approved status as ineligible, even if a copy exists in `GENERATION_RESULTS` or appeared in another chat. During an active request, inspect only that request's guard-scoped outputs when required for its own stage QA. Reuse earlier project images only from approved project folders through the authoritative approved registry. Previous unapproved outputs remain inaccessible and ineligible; do not open their folders even to inspect status or recover a similar scene.

## Agent executes; user directs

The agent performs available project operations; the user gives the goal and decisions. Never instruct the user to move, place, or copy files into project folders, create files or folders, run commands, or type `READY`. Perform those operations yourself when authorized and available. Ask only for genuinely missing input or clarification. If a required source image or mask is absent, politely ask the user to attach it directly in chat; never prescribe a project-folder path.

## Project routing and user authorization

In an active StoryArt workspace, every generation request must use the StoryArt
scenario and available project tools, including brief and named-character
requests. Do not silently replace that route with a bare chat generator call.
Check the current chat for explicit style, reference, and character choices;
show a compact numbered mirror of the complete options, then invoke the
structured native chooser (`request_user_input_async` in Default mode) for all
missing choices together with the identical question and options. The card is
the primary selection surface; the mirror is backup. Never say the menu
appeared unless the chooser call succeeded. If that UI call fails or is
unavailable, state the concrete failure and keep the mirror available for a
numeric reply. A card click or unambiguous matching numeric reply completes the
choice without reconfirmation. `GENERATOR_DEFAULT` and `NO_REFERENCES` are
valid only when the user explicitly chooses them. `PROJECT_STYLE_ONLY` means
the selected project style with no optional user or body-library references;
mandatory approved identity references still apply to a named character.
`USER_ATTACHED_REFERENCES` and `APPROVED_PLUS_USER_REFERENCES` require the
exact attached path/hash plus original source path, chat id, and message id.
Accept derived stage outputs only through QA-validated lineage back to those
same source records. For an existing project
character, resolve the approved registry/profile and attach its approved
assembly plus applicable identity references; otherwise stop and report the
blocker. Leaving the scenario requires the user's direct request, a concrete
warning, and confirmation after that warning. A generator-only route is for
direct edits of user-supplied existing images or an explicitly requested
standalone request outside StoryArt. Report concrete missing-tool failures.
The agent performs available project operations itself. Never tell the user to
move, place, or copy files into project folders, create files or folders, run
commands, or type `READY`. If a required source image or mask is absent, ask the
user to attach it directly in chat, never to place it in a project folder.
External platform safety limits still apply.

Never infer a request to bypass a project rule or leave the StoryArt scenario.
The user must directly ask to leave it. State the concrete likely consequences
and wait for the user's confirmation after the warning every time. External
platform limits cannot be bypassed.

- Prefer minimal, non-breaking changes and avoid unnecessary abstractions.
- Treat every `*_PROJECT_PACK`, `*_GENERATIONS`, `BODY_REFERENCE_LIBRARY`, `POSE_LINE_REFERENCE_LIBRARY`, `GENERATION_RESULTS`, source collection, and image as local user data that must not be committed. The only exception is a user-approved lightweight README preview under `docs/assets/previews`.
- Preserve source images unchanged. Create derivatives as new files and record their provenance in the relevant manifest.
- Use `tools/style_pack_manager.py` for style-pack creation, ingestion, discovery, generation records, approvals, and validation.
- Use `tools/body_reference_manager.py` to append to an existing body-reference library; never rebuild or renumber the library.
- Treat `NATURAL` (`натура`), `SKETCH` (`набросок`), and `OLCHAS` as three explicit body-reference types. Resolve the exact user wording with `python tools\body_reference_type_router.py resolve --text "<exact wording>" --json` before body-reference selection and focus review on every mentioned type. `NATURAL` may define real anatomy and permanent proportions; `SKETCH` is only `POSE_SOFT`; `OLCHAS` is only `ART_BODY_SHAPE_SOFT`, `ART_POSE_SOFT`, or `ART_CAMERA_SOFT`. When combined, `NATURAL` or approved `CHARACTER_BODY` wins anatomy conflicts. Never promote `SKETCH` or `OLCHAS` to `BODY_BUILD_TARGET`, and never infer OlchaS style transfer from a body-reference mention. Canonical aliases and paths are in `config/body_reference_types.json` and `docs/BODY_REFERENCE_TYPES.md`.
- After initial bootstrap, inspect `POSE_LINE_LIBRARY_STATUS`. If it is `NOT_BUILT`, explicitly offer to run `.\scripts\bootstrap.ps1 -CollectPoseLineLibrary`; never start the network collection without the user's approval. If it is `REVIEW_REQUIRED`, offer to continue the contact-sheet review instead of treating unreviewed downloads as usable references.
- `POSE_LINE_REFERENCE_LIBRARY` is independent of the user's `BODY_REFERENCE_LIBRARY` choice. A reviewed line image may be selected as one soft `POSE` reference for joint placement, gesture, balance, foreshortening, and contacts only. It must never act as `BODY`, `BODY_BUILD_TARGET`, face, style, clothes, or physiology authority; attach at most one and preserve canonical character proportions through separate authoritative references and QA.
- Run `tools/generation_risk_assessor.py` before an image-generator call and store the resulting assessment with the local request.
- Start `tools/style_calibration_manager.py` only after the user explicitly requests calibration or consents to a concrete proposal; store the exact authorization quote in the calibration state. QA failure, unproven style, or a disputed PASS may motivate a proposal but never starts calibration. Calibration is optional and must not pause unrelated production while the proposal is pending. Once authorized, default to four rounds, each one composite 2x2 image of four panels; preserve the original generated file and archive a byte-identical copy in `GENERATION_RESULTS`. Keep one face fixed within a round and use a new face for each later round. Score all four by percentages or minimum-to-maximum order, record AI adaptation before the next round, and finalize a reusable conclusion.
- For StoryArt image generation, create `EXECUTION_GUARD.json` with `tools/task_execution_guard.py` to record the goal, visible deliverable, scope, and watchdog thresholds. A standalone request outside StoryArt does not use this guard. The default threshold is 20 active minutes or 12 preflight checkpoints before real execution. Waiting for the user pauses the clock. Crossing the threshold stops unrelated preflight; it does not end or block the task and does not prevent the exact plan, slot, risk, readiness, execution, and result steps defined in `docs/EXECUTION_GUARD.md`.
- Treat workflow permissions as available mechanisms, not authorization to use them. Every substantive action must be necessary for a deliverable in the original request or directly requested by the user; skip optional experiments, comparisons, calibration, diagnostics, helper assets, and technical outputs that are absent from the task contract.
- For a multi-output request, declare every mandatory deliverable with repeated `--required-stage`; a character base uses `FACE_IDENTITY`, `PHYSIQUE_FRONT`, `PHYSIQUE_SIDE`, `PHYSIQUE_BACK`, and `CHARACTER_ASSEMBLY`. Record `STAGE_COMPLETED` after its QA checks; archiving is not a prerequisite for a user-requested result. Before a final answer, `COMPLETE` must succeed; pending stages require continued execution or a concrete blocker.
- Treat ordinary corrective feedback as a change to the active request, not as cancellation. Record `USER_CORRECTION`, reopen only an invalidated stage with `STAGE_REOPENED`, preserve unaffected results, and continue the remaining stages without adding a new approval gate.
- For an adult character-base physique reference, project defaults are an immutable target contract unless the user directly requests a change. Begin with the extreme-micro two-piece and keep that target while adapting prompts, suitable real BODY candidates, attachment synergy, garment-only T/V topology, and staging. A single failed call never unlocks the next rung. Advance only after every approved route for the current target is documented as exhausted: extreme-micro two-piece -> ordinary bikini -> ordinary two-piece swimsuit. Wrong topology is `REJECTED`, not a base for later clothing repair. If all three target routes are exhausted, diagnose age wording, prompt phrasing, source images, role assignments, and attachment synergy; fix the exact call and continue instead of requesting a new clothing concept. Minor characters use only fully age-appropriate neutral clothing and never enter the adult minimal-topology workflow.
- For every new `CHARACTER_BASE`, require an independent `BODY_RENDERING_STYLE` QA pass on FRONT, SIDE, BACK, and CHARACTER_ASSEMBLY. Face or palette similarity is not enough: compare contour hierarchy, skin-value planes, highlights, interior anatomy lines, edge softness, and source medium across the complete body. The accepted FRONT becomes the body-style authority for later views. Real BODY photos define geometry or pose only and never satisfy this style gate. Reject photorealistic, glossy 3D, airbrushed, plastic-skin, generic beauty-render, or face-only style matches.
- Require independent `LIMB_PROPORTIONS` QA on FRONT, SIDE, BACK, and CHARACTER_ASSEMBLY. Overall silhouette or `BODY_PROPORTIONS=PASS` is insufficient. Compare full-resolution total height in heads, hip-to-knee and knee-to-ankle lengths, knee height, ankle width, heel/toe endpoints, and foot length/width against an authoritative source; use a deterministic edge/landmark overlay for style transfers and canonical rebuilds. Record structured evidence with the pass. Reject elongated segments, shifted knees, stretched ankles, or oversized feet. Exclude visible-body style references whose anatomy conflicts even when they were intended as style-only.
- For new adult character-base physique stages, describe swimwear in the prompt and attach no clothing/swimsuit image unless the user directly requested one. `BODY_REFERENCE_LIBRARY` may guide proportions when selected: visually inspect the complete relevant real-photo pool and keep trying suitable candidates. A real body is never anatomically wrong; an image may only be unsuitable for the current role. Use textual `PROMPT_BODY_SPEC` only after the complete relevant pool is exhausted and its reviewed/total counts are recorded.
- Before readiness, record exact user choices for style, reference policy, and character identity in `execution_call.user_selections`, each with its choice and exact `user_quote` from this chat. Ask only for choices not already made in the chat, then wait. A named `CHAR_NNN` must match `REFERENCE_PLAN.character_id` and have the approved `CHARACTER_ASSEMBLY` path/hash physically attached under that role in the exact call. A named character with explicitly selected `GENERATOR_DEFAULT` uses reference-bound `IMAGE_GENERATION` with approved identity refs and no project STYLE slots. `IMAGE_GENERATION_NATIVE_DEFAULT` is limited to an explicitly selected generator-default style, no references, and no named project character. Do not edit managers, tests, or workflow rules inside an image request unless the user explicitly requested that scope change.
- For reference-bound calls, after `prepare-call`, run `EXECUTION_STARTED` against the same reference plan and stage immediately before the generator call; do not add a separate routine `CALL_VALIDATED`. For native/default calls, use the bound prompt/risk contract and readiness/start sequence in `docs/EXECUTION_GUARD.md`. Retain the returned attempt id. Manager registration/archive and file availability (`VISIBLE_RESULT`), user delivery (`RESULT_DELIVERED`), QA, and task completion are separate facts. If a call times out or its outcome is unclear, reconcile that attempt before retrying. A local `STOP` does not cancel a remote call. If the watchdog fires, stop unrelated preparation and continue the exact executable-call sequence while keeping the task active. A timer alone is never a blocker. Follow `docs/EXECUTION_GUARD.md`.
- Every image `EXECUTION_STARTED` must declare the requested-deliverable contract. Never create auxiliary images, comparison variants, or helper generations unless the user requested them. QA, moderation risk, or proportion tuning does not grant permission; use non-generated measurements and overlays instead and tolerate small natural variation.
- Reuse only style, reference, and character choices the user has already made
  in this chat. Ask and wait for any missing choice. The user may explicitly
  choose the generator default style and no references; omission is not a
  choice. For a named project character, resolve the approved registry/profile
  and attach approved identity assets. Stop on a missing identity instead of
  generating a generic substitute. Ask for a specifically requested absent
  source image or mask to be attached directly in chat.
- For new plans, record required `STYLE`, face, body, view, safe-coverage, and clothing-topology QA separately. An unchecked required layer blocks recording; any failed layer makes the output `REJECTED` regardless of other passes.
- After a refusal, record the refusal and any provider reason. Do not run synonym loops or repeat the same call. A timeout or other uncertain outcome requires attempt reconciliation before another call. Keep refusal, unknown outcome, completed result, and delivery as distinct states. Never claim the provider was canceled automatically.
- For a canonical front 3/4 assembly, a low-risk prompt can still be input-blocked by wording, the combined reference set, or both. Assess the exact planned request and attachments; do not probe through repetitive synonym variants, and never omit a required body view. Follow `docs/CHARACTER_ASSEMBLY_3Q_REFERENCE_ROUTING.md`: a provenance-backed generator-safe multiview may preserve FRONT, SIDE, and BACK in one attachment, while an optional successful 3/4 guide is restricted to camera and clothing topology only.
- Archive generated or edited images only when useful to the requested task or explicitly requested; never require an extra archive copy or let it delay delivery.
- Before finishing a code change, run `python -m unittest discover -s tests -v` and compile the changed Python tools.

## Project-local orchestration

- For a substantial character, scene, storyline, style-selection, generation, QA, approval, or storage request, load `skills/storyart-orchestrator/SKILL.md` after creating the task guard. Keep simple read-only questions in the primary agent.
- For a character-free scene, use `--generation-purpose SCENE --character-id NONE` plus `--scene-kind LOCATION|PHENOMENON|ARTIFACT|MIXED` and `--scene-output-use GENERAL_ART|WALLPAPER|PROMO_POSTER`. Do not attach character assembly, face, body, pose, clothes, or auxiliary body references. A character is opt-in only through an explicit approved `CHAR_NNN`.
- Keep `SCENE + NONE` single-pass. Never invent style-synthesis, body, composite, or other service images to fit more references; reduce the set to the five physical slots. Wallpaper QA includes crop/distance/desktop usability. Promo-poster key art reserves a copy-safe zone and receives exact typography deterministically after generation unless the user explicitly asks for generated text.
- Keep orchestration strictly inside StoryArt. Never install its skill or generated style adapters into the global Codex skills directory. Refresh only the selected local adapter with `python tools\storyart_orchestrator.py build-style-skills --style-name "<selected STYLE>"`; adapters remain under ignored `.agents/style-skills` and never contain source images.
- Only when actually delegating, store `ORCHESTRATION_STATE.json` beside `EXECUTION_GUARD.json`; with zero workers skip orchestration init/status/handoff. The primary agent owns user communication, guard transitions, role dispatch, and final decisions. Subagents never edit orchestration state.
- Use only bounded roles from the project skill. Parallelize independent read-only roles only; keep call planning, generation, visual QA, and registration sequential. `GENERATOR_OPERATOR` and `REGISTRAR` are sequential responsibilities of the root, not extra agents or external executors to discover.
- Treat role isolation as a strict path and action contract because subagents share the same filesystem. A role may not expand scope, change project infrastructure, reinterpret another role's verdict, or communicate with the user.
