---
name: storyart-orchestrator
description: Route every StoryArt image-generation request, including a single-frame request, through the project scenario and tools. Also route substantial character, storyline, style-selection, QA, approval, and storage work through bounded project workflows. Use only inside the StoryArt repository; keep simple read-only questions local to the primary agent.
---

# StoryArt Orchestrator

## Current-request boundary and required chooser

For image assets, access only approved project folders and ONE active request
folder bound to the current chat. Never list, search, open or inspect any other
pending/unapproved folder, including historical TEST, DRAFT and REJECTED outputs.
Scene similarity is not request identity. A new chat starts a fresh request;
never inherit previous attempts, failures, budgets or choices from another chat.
Resolve reusable assets only through approved registries, not old request plans.

An approved character profile supplies the STYLE NAME only. It does not choose
style fidelity or BODY_REFERENCE_LIBRARY. Unless both were explicitly selected
in the CURRENT chat, immediately show the standard `Стиль и референсы` chooser
after resolving the style/character, before source selection or preparation.
Keep the three existing 90%+library / 90% without / 70% without options. Do not
infer these choices from a profile, silence, scene wording or another chat.

## Scenario execution backend

StoryArt prepares and validates a call; the built-in `image_gen` tool executes it.
There is no additional StoryArt-named generator or executor to discover or wait
for. After the scenario prepares the exact call and records `EXECUTION_STARTED`,
invoke `image_gen` (`image_gen__imagegen` in code mode), passing the exact prepared
prompt as `prompt` and the physical attachment paths as `referenced_image_paths`.
For a reference-free call, omit reference parameters. Use the tool's returned
image/operation for subsequent scenario steps. This is the scenario's normal
execution path; the ban on a "bare generator" means bypassing preparation.
Never report an unavailable StoryArt executor merely because only `image_gen`
is exposed. Do not add an executor-discovery or availability-check step;
proceed to the prepared call. Handle an actual returned execution error if one
occurs, preserving approved selections while recovering.

## Absolute scenario lock

The user's request authorizes this task only inside the established StoryArt
scenario, in its existing agreed step order. Do not bypass a step, switch to a
bare generator, or substitute another workflow because of a timeout or refusal.
For validator errors, missing profiles, or unavailable project tools, recover
the internal lookup, validation, or tool path and continue whenever an
authorized safe next action remains. Stop only at a genuine external or safety
boundary where no authorized safe action remains. Exit is allowed only after
the user directly requests it, receives a concrete risk warning, and confirms
in the same chat.

Do not abandon an active deliverable by choice. Keep progressing through the
scenario while a safe, authorized next action exists. Treat internal validation,
stage/metadata disagreement, retrieval, QA, registration, and delivery errors
as recovery work: preserve evidence, identify the exact mismatch, repair the
current step, and continue. A successfully returned image is not a failed
generation because registration failed; recover registration against the exact
executed attempt and artifact, without generating a replacement merely to fix
metadata. Never claim success before required QA, registration, and delivery
are complete. Continue a user's correction as a new revision of the active
request, preserving unaffected completed work. Stop only at a genuine external
or safety boundary where no authorized safe action remains, and state the
specific boundary; saving time, effort, or tokens, a partially useful result,
inconvenience, uncertainty, or an internal tool error alone is not such a
boundary. Continue until the requested deliverable is complete or a genuine
external/safety limit leaves no authorized safe action.

Before acting, restore the current request's state from the conversation,
approved project records, and its guard: preserve the request, exact approved
character, bound style, and every already selected parameter. Never re-open a
resolved choice. A clear numeric answer to a shown option is final; carry its
exact displayed mapping forward. If internal validation rejects that mapping,
repair the internal mismatch without asking the user to repeat the choice.

If a choice is truly missing, use exactly one `request_user_input_async` call
with exactly one question and complete options covering all unresolved fields.
Never submit a three-question sequence or a paged “1 of N” questionnaire. After
the chooser call returns, keep the same turn active and wait using
`clock.sleep({duration_ms: 60000})`. If it times out without an answer, repeat
only that sleep in intervals no longer than 60 seconds. While waiting, make no
other tool calls, shell commands, searches, reads, preflight checks, commentary,
or final response. Resume the exact same step as soon as the user answers; never
ask again about a resolved choice.

## User-request authority for image generation

A correction to the current delivered art in the same chat continues that request via USER_CORRECTION, even after COMPLETE. Keep its folder binding and explicit style/library choices until a new request replaces it; do not show the chooser again or classify the current delivered art as unrelated history. This never authorizes access to another request folder.


An explicit image request authorizes the work, but not unstated image choices. Every generation request must run through the StoryArt scenario and project tools. Reuse style, reference, and character choices only when the user has already selected them in this chat. For missing choices, show one compact numbered text mirror of the exact options as a backup, then immediately invoke the structured native chooser (`request_user_input_async` in Default mode) with the same question and options. The chooser is the primary selection surface; the numbered mirror is redundant recovery only. Never claim that a menu appeared unless the chooser call succeeded. If the chooser call actually fails or is unavailable, state that concrete tool failure and keep the numbered mirror available for a reply; do not pretend the mirror is the native menu. The user may choose in the card or reply with the matching number; accept the first unambiguous answer and continue without reconfirmation. The user may explicitly choose generator-default style and no references. Resolve named project characters through the approved registry/profile and attach the approved assembly and applicable identity references. If identity or required assets cannot be resolved or attached, stop and report the blocker instead of generating a substitute. Leaving the scenario requires the user's direct request, a concrete warning, and confirmation after that warning. External platform limits cannot be bypassed.

Unless the user explicitly requests reference search, do not search for optional
scene, pose, or body-library references. Treat optional references as declined
and use the exact user scene text together with approved character identity
assets. This default never permits guessing identity or omitting mandatory
approved identity references. If the user requests a search, keep it within the
scope they specified and within the StoryArt scenario.

In an active StoryArt workspace, every generation request must enter the StoryArt scenario and use available project tools, including brief and named-character requests. Never silently replace the scenario with a bare chat-image generator call. Check the current chat for style, reference-policy, and character-identity choices. For missing choices, show the numbered backup mirror and invoke `request_user_input_async` immediately with the identical native options; the chooser is primary and the mirror is backup. If the actual chooser call fails, report that failure and keep the mirror available for a matching numeric answer. Generator-only execution is for direct edits of user-supplied existing images or an explicitly requested standalone request outside StoryArt. If a required project tool is unavailable, report that failure and do not report project execution as successful.

Never infer a request to bypass a project rule or leave the scenario. The user must directly ask to leave it. Explain the concrete likely consequences and wait for confirmation after the warning every time. External platform limits cannot be bypassed.

## Approved-generation source boundary

Do not search, enumerate, inspect, or surface prior unconfirmed generations as candidate bases, references, or continuation options. Treat historical `00_PENDING` folders and manifest statuses `TEST`, `STAGING`, `REJECTED`, `DRAFT`, or any other non-approved status as ineligible, even if a copy exists in `GENERATION_RESULTS` or appeared in another chat. During an active request, inspect only that request's guard-scoped outputs when required for its own stage QA; such outputs cannot become reusable references for another request. Reuse earlier project images only from approved project folders through the authoritative approved registry. Previous unapproved outputs remain inaccessible and ineligible; do not open their folders even to inspect status or recover a similar scene.

### Resolve named characters before reporting a blocker

When a request names a project character, resolve it from approved registry data
before saying that its profile is missing or asking the user to attach identity
art. Do not use `rg` or a generic file search to locate these records: generation
libraries are often ignored by Git and default search skips them. Run the
registry-only resolver, which reads `*_GENERATIONS/CHARACTER_REGISTRY.csv` rows
with `status=APPROVED` and validates the profile and registered identity assets;
it does not search historical generations:

```powershell
python tools\style_pack_manager.py resolve-character --workspace . --name "<name as requested>" --json
```

Use the returned canonical `style_name`, `character_id`, profile, assembly,
face, and body paths for the existing-character route. The resolver accepts
common Russian case forms (for example, `Шанса` resolves to canonical `Шанс`)
and explicit `CHAR_NNN` IDs. Preserve the user's named identity when preparing
the request and bind the approved assembly plus required identity references
to the exact call. A blocker is justified only when the resolver reports no
approved match, a registered profile/assets fail validation, or execution of
the resolver itself returns a concrete error. If multiple approved matches
remain, ask only which matching project/style the user means. Never claim the
scenario tool is unavailable before attempting its documented project CLI in
the active workspace and recording the actual failure.

The resolver must also supply the effective confirmed character profile, not
just a catalog. Every user-confirmed profile addition is persistent input for
later requests: arbitrary nested profile facts, approved wardrobe/accessory
defaults, and explicitly active face/body variants.
Treat numeric and boolean values, zero, false, and explicit null as profile
data too; an accepted value must not disappear because it is not a string.
Use the shared profile confirmation operation (`confirm-profile`) after the user's confirmation;
the agent performs the operation without asking for a second confirmation or
requiring the user to edit files. Image approval commands use the same state.
Do not implement a confirmed update by editing YAML or a role list alone.
Use the `confirm-profile` command contract in `docs/PROJECT_ORCHESTRATOR.md`:
submit the confirmed patch or candidate YAML, the resolver's expected revision,
a stable operation ID, and the actual user confirmation quote. Keep that
operation ID for retries. This is an internal agent operation, not an extra
question or a manual user step.

Preparation consumes the current confirmed revision automatically. Preserve
all effective profile facts in the manager-built executable prompt and attach
the applicable active visual defaults through normal source review and role
validation. An explicit scene override affects only that scene. Saving a
variant as an alternative does not activate it; an already established
default resolves the choice even if other approved alternatives exist.
Unconfirmed edits never replace the last confirmed revision. If a confirmed
revision changes after preparation, rebuild the stale plan using that revision
without asking the user to repeat the confirmation. Never silently discard a
default because of an attachment limit or a validation failure.

The resolver also returns the approved character-asset catalog by role. Read
the complete catalog and its profile-index diagnostics; `profile_schema`, an
empty profile list, or the canonical `identity_assets` object alone does not
prove that wardrobe, accessories, or approved variants are absent. For a
requested costume or accessory, bind the matching approved role asset through
the scenario manager when one eligible match exists. Preserve the user's
already selected asset and the effective profile's active default; when several
eligible alternatives remain and neither a scene selection nor a confirmed
default exists, ask only which one they want. If the profile index is
stale, use the exact active approved registry record and report the mismatch
for repair. If a registered file is missing or invalid, state that concrete
error. Never ask the user to reattach a character card when its approved
registry already resolves the required asset, and never infer approval from a
file's presence in a role folder alone. Face/body variants remain optional
role assets and must never replace canonical face, body, or assembly identity.

## Agent executes; user directs

The agent performs available project operations; the user gives the goal and decisions. Never instruct the user to move, place, or copy files into project folders, create files or folders, run commands, or type `READY`. Perform those operations yourself when authorized and available. Ask only for genuinely missing input or clarification. If a required source image or mask is absent, politely ask the user to attach it directly in chat; never prescribe a project-folder path.

For a direct one-step edit of user-supplied existing image(s), including a chat-attached mask, go directly to the image-edit tool. Do not require a task guard, REFERENCE_PLAN, local saves, a style menu, risk receipt, or QA receipt. If a guard is voluntarily used, choose IMAGE_EDIT. This direct-edit bypass remains in effect. For new generation and project workflows, the StoryArt scenario and its required selections are mandatory. A direct request authorizes the goal, but omission is not a style/reference choice and does not authorize leaving the scenario. Continue only after required choices have been bound to the exact executable call and project assets are resolved. Leaving the scenario requires the user's direct request, a concrete warning, and the user's confirmation after that warning. External platform limits still apply. Archives remain optional and never gate delivery.

Act as the only user-facing coordinator. Keep the original request,
`EXECUTION_GUARD.json`, applicable rules, and existing StoryArt managers
authoritative. Read `AGENTS.md` and the task-relevant section of
`docs/PORTABLE_GENERATION_RULES.md`; use local `docs/GENERATION_RULES.md` only
when it is explicitly present and applicable. Do not wholesale-read historical rules. Apply
`docs/EFFICIENT_WORKFLOW.md` only to orchestration and evidence reuse.

## Start

On Windows, run project tools with `./scripts/storyart.ps1 <tool_name> <args>`
(for example, `./scripts/storyart.ps1 style_pack_manager resolve-character ...`).
The launcher selects an installed Python with the project dependencies and
preserves tool arguments. Do not cycle through broken interpreters, install
packages during an image request, or substitute an old request for a failed
profile lookup. Tool commands written as `python tools/<name>.py` below use this
launcher on Windows.

1. Read `AGENTS.md`, the active guard, and only the relevant workflow sections.
2. Only when actually delegating work, initialize `ORCHESTRATION_STATE.json`
   beside the guard. With zero workers, skip orchestration init/status/handoff:

```powershell
python tools\storyart_orchestrator.py init `
  --state "<request-dir>\ORCHESTRATION_STATE.json" `
  --guard "<request-dir>\EXECUTION_GUARD.json"
```

3. An adapter is an optional routing index, not a preparation gate. If approved
   profile/source paths are already resolved, use them directly. When using an
   adapter, run the builder only when that adapter
   is missing or stale for its actual source inputs. Pass the exact selected
   style name to refresh only that adapter and its index entry; do not rebuild
   every style adapter as routine startup:

```powershell
python tools\storyart_orchestrator.py build-style-skills --style-name "<selected STYLE>"
```

4. If needed, read only the selected adapter under `.agents/style-skills/`.
   Never install it globally or validate all adapters during image preparation.

## Style and reference chooser

Resolve a named character before asking about style. If its single approved
profile is bound to a project style, inherit that exact style as the default;
reuse only the style name. Fidelity and BODY_REFERENCE_LIBRARY still require
the current-chat chooser unless already explicitly answered. If the profile explicitly records
the generator default, preserve that. If the user asks to change style, or the
character has no unique recorded style, resolve the available names with
`style_pack_manager.py list-styles --json` and show the actual style names.
Never ask the vague question “which project style?” when named choices can be
listed. For a missing style choice with no bound character style, collect the
style choice before constructing the style-specific plan.

Collect any remaining independent style/reference choices with the existing
native context menu: use `request_user_input_async` in Default mode. First show
the exact numbered text mirror of the generated options as backup, then invoke
the native chooser immediately with the same title/question/options. Do not
replace an available eligible native chooser with prose or switch modes to
expose another tool. If the native call fails or is absent, state the actual
failure and leave the matching numbered list available for a reply. Do not
claim the native menu appeared unless its call succeeded.

Use exactly one chooser question for the missing choices, with complete options
that map every unresolved field; do not pass separate questions that produce a
multi-page “1 of N” questionnaire. After invoking the native chooser, keep the
turn active by calling `clock.sleep({duration_ms: 60000})`; if it times out
without a user answer, repeat only the sleep at intervals no longer than 60
seconds. Make no other tool call, search, file read, preflight, progress or final
message while awaiting the structured answer. On answer, continue the scenario
with that selection. Silence is not a choice.

When the user selects a displayed option, that answer completes the choice.
Immediately carry its exact mapped parameters forward in the active scenario.
Do not re-ask or require a paraphrased confirmation because a validator or
internal manager rejects its mapping; repair that internal mismatch while
preserving the selected option. Ask again only when the user answer itself is
ambiguous or a concrete external blocker makes the selected operation
impossible.

Reuse already selected fields. Present the remaining choices together in one
interaction, with short labels and complete descriptions. Each profile must
name the exact style, fidelity, reference policy and, when applicable, the
resolved approved character identity. For machine binding, append the exact
semicolon-separated fields `style=PROJECT_STYLE:<style>; reference_policy=<policy>;
character=<CHAR_NNN or NONE>` to each complete option description. The manager
records them as `resolved_user_selections` and checks them again at prepare-call.
Keep readable prose explaining these values in the displayed description.
Use the guard's reference-policy IDs: PROJECT_STYLE_ONLY,
APPROVED_CHARACTER_REFERENCES, APPROVED_PLUS_USER_REFERENCES,
USER_ATTACHED_REFERENCES, or (only with GENERATOR_DEFAULT) NO_REFERENCES.
PROJECT_STYLE_ONLY allows the selected project STYLE plus mandatory identity
assets for a named character, and no optional user/body-library references.
Use USER_ATTACHED_REFERENCES or APPROVED_PLUS_USER_REFERENCES only when
matching physical user-attached slots have current-chat evidence bound by
stored path/hash, original source path, chat id, and message id through
`--user-reference-evidence-json`. A derived stage output counts only when its
QA-validated snapshot and pack lineage resolve to that same source evidence.
Preserve a character
already named in the request as a locked identity; never offer NONE for it.
A named character always retains its
mandatory approved identity references; declining BODY_REFERENCE_LIBRARY only
declines that auxiliary library, not the character's identity assets.

For an already style-bound approved character such as Шанс, show that exact
style name in any reference/fidelity choice descriptions and bind it to every
option; do not label it merely “project style.” For an unbound style, list
actual discovered style names and collect the style choice before planning.
For a new standard image-generation choice, use one native question titled
`Стиль и референсы`. State the resolved character and exact profile-bound style;
explain that the percentage means fidelity to that style and that approved
character-identity references remain attached in every option. Use exactly
these three presets, replacing `{style}` and `{character}` with the resolved
values:

1. `90% стиля {style} + использовать BODY_REFERENCE_LIBRARY (рекомендуемый профиль StoryArt)`
2. `90% стиля {style}, без BODY_REFERENCE_LIBRARY`
3. `70% стиля {style}, без BODY_REFERENCE_LIBRARY — более свободная интерпретация`

Bind them respectively to `(90, SELECTED, BODY_LIBRARY_ONLY)`,
`(90, DECLINED, APPROVED_CHARACTER_REFERENCES)`, and
`(70, DECLINED, APPROVED_CHARACTER_REFERENCES)` for fidelity, auxiliary
library decision, and reference policy. Keep the named character and mandatory
approved identity assets in every option. Do not show ranges such as
`70–100%`, vague labels such as “стиль профиля персонажа,” or the phrase
“референсы тела” in place of the explicit `BODY_REFERENCE_LIBRARY` decision.
Use the exact bound style name. Keep stable `OPTION_N` ids; `OPTION_1` has no
meaning outside the exact mapping shown in this menu. For `CHARACTER_BASE`
only, add the established note that BODY_REFERENCE_LIBRARY applies only to
later physique stages and does not start calibration or pose collection. Do
not add that note to an ordinary scene request. The native tool's built-in
free-text Other response maps to `CUSTOM`; do not add a fourth option when the
tool supports only three. Custom answers must explicitly resolve missing
parameters before execution. Previously accepted menu answers always retain
their original displayed mapping; this preset applies only when opening a new
standard chooser.

Do not author these option labels or mappings from memory. After resolving the
approved profile and confirming that no same-chat choice already exists, call
`python tools\style_pack_manager.py startup-menu-template --style-name
"<exact profile style>" --character-id <CHAR_NNN> --character-name
"<approved name>" --generation-purpose SCENE --json` (use
`--generation-purpose CHARACTER_BASE` only when creating a new character
identity kit). Pass the returned `title`, `question`, and each option `label`
verbatim to the single native chooser. Retain each matching option's `id`,
`description`, `fidelity`, `aux_body_decision`, and
`resolved_user_selections` for the existing manager preparation after the
user chooses. This command is a deterministic menu presenter, not a new user
decision or scenario step.

Record the actual surface: a selection made in the native card uses
`--startup-selection-mode NEW` and `--startup-menu-surface NATIVE_CONTEXT_MENU`;
a numeric answer to the mirrored list uses `USER_CONFIRMATION` and
`TEXT_NUMBERED_MENU`. The mirror and card share identical option mappings.
Never claim a native menu was shown merely because a profile was prepared: the
CLI surface defaults to empty and requires an explicit value after the actual
interaction. Preserve the displayed descriptions with `--startup-option`, the exact answer with
`--startup-choice-user-quote`, and its chat/message provenance. A reply `1`
selects **all** fields explicitly stated in OPTION_1, including its reference
decision. Do not ask a second reference question about that resolved profile.
For an unanswered field that was absent from the displayed option, ask only
that field; do not infer it from a style-only answer.

The existing prepare-call automatically carries the resolved selections and original answer into
`execution_call.user_selections` (`style`, `reference_policy`, `character`),
retaining the displayed option mapping as evidence. Do not request those fields again. Optional --user-selections-json must equal the resolved selections exactly. Legacy plans without a mapping require the chooser for genuinely missing choices. Use the existing same-chat
REUSE contract on subsequent frames unless the user changes a choice.
`GENERATOR_DEFAULT` and `NO_REFERENCES` remain explicit selectable choices
when compatible with the request; never assume them or offer no references
for a named project character. They use their existing native-default route,
not a fabricated project style pack or a BODY_REFERENCE_LIBRARY profile.

## Prepare and record image calls

This section describes the mandatory StoryArt route for image generation.
Reuse only style and reference choices already made by the user in this chat.
Use request_user_input_async for missing choices before constructing an executable call; do not duplicate the chooser with prose questions. The
user may explicitly choose the generator default and no references.

Use reference-bound `IMAGE_GENERATION` for named project characters and
reference-based calls. A named character may explicitly use `GENERATOR_DEFAULT`
style through this reference-bound lane, with no project STYLE slots and with
approved identity references attached. Use `IMAGE_GENERATION_NATIVE_DEFAULT`
only when the user explicitly selected generator default, no references, and
no named project character. Never route a named project character through the
no-reference lane.

- Before `READY_FOR_EXECUTION`, require explicit style, reference-policy, and
  character-identity choices in `execution_call.user_selections`, each with its
  choice and exact `user_quote` from this chat. Use request_user_input_async only for choices not already
  made in this chat, then wait. The guard enforces `GENERATOR_DEFAULT` and
  `NO_REFERENCES` only when selected. For `CHAR_NNN`, bind the matching approved
  `CHARACTER_ASSEMBLY` path/hash under that role in the exact physical call; a
  missing or mismatched identity is a blocker. When a specifically requested
  image or mask is absent, ask the user to attach it directly in chat.
- When showing source thumbnails during planning, label each as REVIEW ONLY - NOT
  SELECTED or identify its exact planned role and subject. Never let a preview imply
  that an image is attached to the executable call. For an existing character, a
  female anatomy reference cannot be assigned to his body or identity roles; a
  companion reference must be explicitly scoped to that separate subject, or be
  omitted when the current plan cannot represent that scope.
- Resolve target anatomy from explicit current-request wording or reviewed
  character metadata and retain its evidence. If unknown, ask before showing or
  attaching full-body references; never guess from a name or image.
- `style_pack_manager.py prepare-generation` creates the request plan in
  `PREPARED_AWAITING_EXECUTABLE_CALL`; it does not authorize a generator call.
- For an approved-character `SCENE`, pose, clothing, lighting, background, and
  composition may come from the exact user scene text. If one of those optional
  local roles is not selected, `prepare-generation` records it as an explicit
  prompt source; keep the described detail in the exact `prepare-call` prompt.
  This does not relax the required project style, approved identity assembly,
  or applicable body identity references, and never selects
  `BODY_REFERENCE_LIBRARY` or user-added references.
- Select reference sources in this order: explain the style from its written profile,
  inspect the existing style/reference matrix or contact sheet, shortlist a few
  compatible candidates, then inspect only the exact selected full-resolution
  sources. Do not visually review an unrelated full candidate pool as a
  preparation prerequisite. `prepare-generation` binds each selected source
  review to its exact path and SHA-256 under every active role; a role count
  cannot stand in for that source-bound evidence. A selected source without a
  matching review declaration remains blocked.
- For the current stage, save the resolved inputs and observed source ratings
  once, then use `python tools/generation_request.py --request-file <request.json>
  --call-file <call.json>`. This bundles plan preparation, `resolve-call`, exact
  prompt risk assessment and `prepare-call` through the existing managers.
  Do not run those commands again individually. For a call-only correction,
  use `--finalize-only`; supply the explicit stage for an already READY
  multi-stage plan. Follow `docs/EFFICIENT_WORKFLOW.md` under "One preparation
  request" for the schema. Separate manager commands remain recovery tools.
- After `prepare-call` records readiness, run `EXECUTION_STARTED` with that
  same plan and stage immediately before the generator operation. The guard
  derives the canonical stage and locked invariant assertions from its validated
  ready binding; if supplied explicitly, they must match that binding exactly.
  A separate `CALL_VALIDATED` checkpoint is optional.
- Start only the ready plan and stage. Retain the returned attempt id. Register
  the output with `record-generation`, which requires the matching attempt,
  plan, character, and stage, archives the original, applies the required QA,
  and records result availability.
- Before registration, inspect the actual full-resolution output against the
  exact executable prompt and the approved identity/style references applicable
  to the stage. Complete three independent visual layers: anatomy and body
  mechanics, visible defects/artifacts across the whole image, and a checklist
  assessing every explicit prompt constraint. Supply `--visual-review-json`
  with an explicit reviewer and bindings for request, attempt, task revision,
  output hash, executed prompt hash, and immutable executed-plan hash. Anatomy
  is `NOT_APPLICABLE` only when no anatomy is visible, with a specific reason;
  full-body, physique, and assembly stages always require an applicable anatomy
  review. Break the exact executed prompt into its separate sentence and clause
  segments and record one evidence-backed PASS/FAIL item for each segment; the
  manager checks ordered coverage against the exact prompt text. A failed layer records `REJECTED` and preserves findings in the QA
  contract/receipt for correction inside the authorized scenario. Do not review
  unrelated or pending art.
- A tool event with status `completed` confirms completion of the tool call,
  not provider success or image availability. Inspect its returned outcome:
  a provider-success receipt or returned image artifact supports continuing
  with result retrieval, required QA, `record-generation`, and delivery.
  Never describe an executed call as not started. If retrieval, QA,
  registration, or delivery fails, name that exact stage and preserve the
  existing attempt/output without automatic regeneration. Report provider
  failure or refusal only from actual provider evidence; reconcile an unknown
  outcome before retrying.
- Recovery is mandatory after every recoverable internal failure: use the
  persisted call snapshot and attempt/artifact lineage to repair validation or
  registration, then continue the still-active request. Do not close the task,
  ask the user to repeat a choice, or leave the workflow merely because a
  manager's current state disagrees with its own executed snapshot. If the
  provider result exists, do not invoke generation again to repair bookkeeping.
  If the user corrects the requested scene, record a new request revision and
  carry it through the same scenario. Report a blocker only when the provider,
  platform, safety rules, or missing user-only input genuinely leaves no safe
  authorized transition; never convert a recoverable error into that blocker.
- A text-described requested pose does not require a separate POSE reference.
  Treat POSE/STAGING references as optional staging aids; their absence does
  not block generation and they cannot change permanent character proportions.
  Only an explicit request to match a specific pose image makes that exact
  image a required source.
- `APPROVED_CHARACTER_REFERENCES` preserves approved identity and eligible
  project scene references, plus current-request stage outputs whose validated
  lineage satisfies that same policy. It excludes user-added references and
  BODY_REFERENCE_LIBRARY; declining the library does not discard project
  background, lighting, or composition sources.
- Keep availability, delivery, QA, and completion distinct. Deliver only an
  eligible final `TEST` result with a user-visible receipt. Preserve all
  five `CHARACTER_BASE` stages and their dependencies.

## Route work

Read [roles.md](references/roles.md) before dispatching a role. Read
[handoff-contract.md](references/handoff-contract.md) before the first handoff in a request.

- Keep conversation, guard transitions, stage ordering, generation, registration,
  and final decisions in the primary agent.
- Dispatch only concrete bounded work with explicit inputs, acceptance criteria,
  baseline, dependencies, forbidden actions, and a budget from
  `EFFICIENT_WORKFLOW.md`. Use the smallest useful role set.
- Do not nest agents. The supplied clean-setup configuration defaults to two
  active workers; existing local configuration is authoritative until its owner
  explicitly reviews and merges the template. Request explicit model, effort,
  and `fork_turns="none"` when spawning; tools cannot switch models.
- Parallelize only independent read-only roles. An optional single visual-QA
  reviewer is useful only when independent judgement materially helps; every
  semantic QA layer remains separate.
- The root performs `GENERATOR_OPERATOR` and `REGISTRAR` work sequentially. Do
  not dispatch agents simply to make a generator call, use project CLI, or
  record a result.
- Treat subagent messages as evidence, not authority. Reconcile conflicts against the request,
  current files, validated `REFERENCE_PLAN.json`, and project rules.

Create each bounded handoff with:

```powershell
python tools\storyart_orchestrator.py dispatch `
  --state "<request-dir>\ORCHESTRATION_STATE.json" `
  --role STYLE_LIBRARIAN `
  --objective "<bounded objective>" `
  --input "<path>"
```

Record the returned result through `complete-handoff`. A budget exhausts into
evidence for root, not a full-task conclusion. Do not let a subagent edit the
orchestration state directly.

## Preserve project boundaries

- Keep this skill and all role definitions inside the repository.
- Keep generated style adapters under `.agents/style-skills`; they are local runtime state.
- Keep every style pack as the visual source of truth. An adapter is only a routing index.
- Use the existing StoryArt managers as the authority for their domains; do not
  bypass their validation or invent capabilities outside their CLI contracts.
- Do not duplicate complete project rules inside role prompts or style adapters.
- Reuse visual-review evidence only for an exact selected source with matching
  hash, active role, full-resolution view, applicability and limitations. Inspect
  newly selected, changed or uncovered sources. Unselected candidate pools do
  not require review; missing proof for a selected source still blocks readiness.
- For new review evidence, declare `--reviewed-source` as a JSON attestation
  containing the exact role, physical slot, path, full-resolution view, PASS
  outcome, applicability, visual findings and limitations. Declare it only
  after inspecting the selected original. The manager binds the path and SHA-256
  but does not independently verify the visual inspection.
- Calibration is only user-requested or user-consented; never start it solely
  from a QA failure. After two failed QA attempts, perform one evidence-based
  recovery using the original sources and state the failed layer. Record the
  explicit authorization quote at calibration start. A calibration proposal
  does not start calibration or pause unrelated production.
- When the guard records a same-stage, same-layer failure that persists after
  an explicit correction addressed the first, dispatch the one-shot `ESCALATION_ORCHESTRATOR` profile
  only while that incident is due. It is registry `ESCALATION_ORCHESTRATOR` (Sol 6.1 High) exceptional error
  handling, read-only, and returns one bounded Sol work order. It must
  not use tools, generate, test, edit, QA, approve, or spawn; root dispatches
  the recommended executor. It is never an ordinary diagnostic or per-frame role.
- Use `style-readiness` only to surface a consent request when five unique
  QA-passed outputs exist for an unformed/unfinalized style. It never starts
  calibration or generates test art.
- If native subagents are unavailable, execute the same contracts sequentially in the primary
  agent and keep the same write ownership.

## Finish

Require all dispatched handoffs to reach `DONE`, `REJECTED`, or a concrete `BLOCKED` state.
Run `storyart_orchestrator.py status` only if handoffs were dispatched; complete the execution guard only when every
mandatory StoryArt stage is complete.

## Routing precedence

Use `config/model_routes.json` as the role authority and
`docs/MODEL_PROFILES.md` for activation and experiments. Sol 6.1 Medium owns the
user contract and acceptance; bounded implementation/discovery uses Low;
fresh independent review uses Medium; exceptional diagnosis uses High.
Luna 6 is outside normal production routing. Sol 6.1 Low root and Luna 5.6 Low
mechanical workers require an explicit opt-in trial. Normally zero workers,
one when useful, at most two independent workers. No full-history forks,
nesting, or agents merely for generator/archive/CLI operations.
Reuse source evidence only when hash, role, view, applicability and limitations
match; inspect changed or uncovered sources. Packet ceilings return evidence
to root and never terminate the user's image goal. This changes orchestration,
not artistic, identity, safety or QA requirements.
